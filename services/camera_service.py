import cv2
import numpy as np
import os
from functools import lru_cache
from pathlib import Path
from typing import Optional, List, Tuple, Callable
import logging
import threading
import time
from config.settings import settings

logger = logging.getLogger(__name__)

_VIDIOC_QUERYCAP = 0x80685600
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001

#: The capture format this service asks for.
#:
#: The device this project runs on ("ACER HD User Facing", /dev/video0)
#: publishes exactly two formats at 1280x720: MJPEG and YUYV. Measured on this
#: machine, MJPEG delivers **13.0 fps** where YUYV delivers **8.7 fps** -- YUYV
#: is a packed 16-bit-per-pixel stream that the host has to unpack on the CPU,
#: while MJPEG is decoded by the camera itself. The request is only a request:
#: a device that does not offer MJPEG is left on whatever it does offer, and
#: the format actually negotiated is read back and logged.
PREFERRED_FOURCC = "MJPG"


@lru_cache(maxsize=None)
def _v4l2_capture_capability(index: int) -> Optional[bool]:
    """Whether ``/dev/videoN`` can deliver video, as the kernel itself reports it.

    One physical UVC camera normally exposes *several* ``/dev/videoN`` nodes for
    the same hardware, and the extra ones are not cameras: on this machine the
    built-in Chicony webcam publishes ``/dev/video0`` (a real stream) and
    ``/dev/video1``, a metadata/control node with no capture capability at all
    (``device_caps`` = ``0x4a00000``, i.e. streaming and metadata but not
    ``V4L2_CAP_VIDEO_CAPTURE``).

    The distinction matters because OpenCV's own backends disagree about it.
    The V4L2 backend reads the capability and refuses the node --
    "can't open camera by index" -- while the FFMPEG and obsensor backends open
    it anyway and hand back frames that are not video, accompanied by
    ``ioctl(VIDIOC_QBUF): Bad file descriptor``. Probing by ``isOpened()`` alone
    therefore lists the metadata node as a second camera that cannot be started
    reliably.

    Returns ``True``/``False`` when the kernel answered, and ``None`` when the
    question does not apply (no ``/dev/videoN`` node, non-Linux platform,
    missing permission, old kernel without ``device_caps``). Callers must treat
    ``None`` as "unknown, do not skip" so the query can only ever remove nodes
    the kernel has already proven are not cameras.
    """
    node = Path(f"/dev/video{index}")
    if not node.exists():
        return None
    try:
        import ctypes
        import fcntl

        class _Capability(ctypes.Structure):
            # struct v4l2_capability, with the trailing reserved array included
            # so the kernel sees the full, correctly sized structure.
            _fields_ = [
                ("driver", ctypes.c_char * 16),
                ("card", ctypes.c_char * 32),
                ("bus_info", ctypes.c_char * 32),
                ("version", ctypes.c_uint32),
                ("capabilities", ctypes.c_uint32),
                ("device_caps", ctypes.c_uint32),
                ("reserved", ctypes.c_uint32 * 14),
            ]
    except ImportError:  # no ctypes/fcntl on this platform
        return None

    try:
        handle = os.open(node, os.O_RDONLY)
    except OSError:
        return None
    try:
        capability = _Capability()
        fcntl.ioctl(handle, _VIDIOC_QUERYCAP, capability)
    except (OSError, ValueError):
        return None
    finally:
        os.close(handle)

    # device_caps appeared in V4L2 2.6; when it is zero the device-level
    # capabilities field holds the answer instead.
    caps = capability.device_caps or capability.capabilities
    return bool(caps & _V4L2_CAP_VIDEO_CAPTURE)


class CameraService:
    def __init__(self):
        self.cap: Optional[cv2.VideoCapture] = None
        self.camera_index = settings.DEFAULT_CAMERA_INDEX
        self.width = settings.CAMERA_WIDTH
        self.height = settings.CAMERA_HEIGHT
        self.fps = settings.CAMERA_FPS
        self.is_running = False
        self.capture_thread: Optional[threading.Thread] = None
        self.frame_callback: Optional[Callable[[np.ndarray], None]] = None
        self.latest_frame: Optional[np.ndarray] = None
        self.frame_lock = threading.Lock()
        self.available_cameras: List[int] = []
        # Non-capture /dev/videoN nodes already explained to the user, so a scan
        # does not repeat the same explanation once per backend.
        self._reported_non_capture_nodes: set[int] = set()
        # Monotonic counter of captured frames; see get_latest_frame_with_id.
        self._frame_id = 0
        #: Frames actually delivered per second, measured rather than requested.
        #: The V4L2 backend reports the *requested* frame rate back from
        #: ``CAP_PROP_FPS`` whether or not the device can honour it, so the
        #: requested number and the delivered one are two different facts and
        #: only the second one says how fast the scanner will actually run.
        self._measured_fps: float = 0.0
        self._frames_in_window: int = 0
        self._window_started_at: float = time.monotonic()

    def _try_open(self, index: int):
        """Open a device, falling back across backends.

        V4L2 is tried first because it is the native Linux backend, but it can
        refuse an index that the default backend opens happily (observed on this
        machine: /dev/video1 reads fine via the default backend but V4L2 reports
        "can't open camera by index"). Probing only one backend therefore hides
        working cameras from the device list.

        Falling back has a cost, though, and it is why the kernel is asked
        before any backend is tried: the nodes that V4L2 refuses are exactly the
        ones a lenient backend will open and fill with non-video frames. A node
        the kernel reports as lacking ``V4L2_CAP_VIDEO_CAPTURE`` is a metadata or
        control node of the same physical camera, so it is not opened at all --
        which also keeps OpenCV from printing "can't open camera by index" and
        ``ioctl(VIDIOC_QBUF): Bad file descriptor`` on every scan.
        """
        if index < 0:
            # OpenCV will happily hand back a handle for a negative index,
            # silently opening an unintended device.
            return None
        if _v4l2_capture_capability(index) is False:
            if index not in self._reported_non_capture_nodes:
                self._reported_non_capture_nodes.add(index)
                logger.info(
                    f"/dev/video{index} is a node of an existing camera but reports no "
                    "video capture capability (metadata/control), so it is not offered "
                    "as a camera."
                )
            return None
        backends = [cv2.CAP_V4L2, cv2.CAP_ANY] if hasattr(cv2, "CAP_V4L2") else [cv2.CAP_ANY]
        for backend in backends:
            cap = cv2.VideoCapture(index, backend)
            if cap.isOpened():
                return cap
            cap.release()
        return None

    @staticmethod
    def _probe_upper_bound(fallback: int) -> int:
        """Highest index worth probing, derived from the real device nodes.

        On Linux a camera index maps to ``/dev/videoN``, so probing past the
        highest existing node is guaranteed to fail. Those failures are not
        quiet: OpenCV's V4L2, FFMPEG and obsensor backends each print to stderr,
        including at ``[ERROR]`` level, and on OpenCV 5 the
        ``getLogLevel``/``setLogLevel`` bindings that :meth:`scan_cameras` uses to
        mute them no longer exist. On a machine with two cameras that meant four
        guaranteed-failing probes and roughly twenty lines of stderr every time
        the scanner page opened.

        Reading the device nodes bounds the scan exactly -- it is not a heuristic
        and cannot hide a camera that exists. A floor of 2 keeps index 0 (and a
        second backend) reachable even where the node list is unavailable, and
        that floor never exceeds the caller's own ``fallback``, so
        ``max_index`` stays a true upper bound.

        Returns ``fallback`` unchanged on platforms without ``/dev/videoN``.
        """
        node_dir = Path("/dev")
        if not node_dir.is_dir():
            return fallback

        highest = -1
        try:
            for entry in os.scandir(node_dir):
                name = entry.name
                if not name.startswith("video"):
                    continue
                suffix = name[len("video"):]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        except OSError:
            return fallback

        if highest < 0:
            # No V4L2 nodes at all: keep a minimal probe so an OpenCV backend
            # that is not V4L2 still gets a chance.
            return min(fallback, 2)
        return min(fallback, max(2, highest + 1))

    def scan_cameras(self, max_index: int = 6) -> List[int]:
        """Enumerate readable devices, trying several backends per index.

        Probing only V4L2 hides cameras that the default backend can open; see
        :meth:`_try_open`.

        An index counts only if the kernel agrees the node can capture video, so
        the extra ``/dev/videoN`` nodes that UVC drivers publish for the same
        physical camera are excluded here rather than listed as cameras that
        fail to start later.

        ``max_index`` is an upper bound, not a mandate: on Linux it is reduced to
        the number of real device nodes by :meth:`_probe_upper_bound`, because
        every probe beyond them is a guaranteed failure that OpenCV reports on
        stderr. OpenCV 5 dropped ``getLogLevel``/``setLogLevel`` from the Python
        bindings, so silencing those messages programmatically is no longer
        possible and not probing them is the only remaining remedy.
        """
        self.available_cameras = []
        upper = min(max_index, self._probe_upper_bound(max_index))
        get_level = getattr(cv2, "getLogLevel", None)
        set_level = getattr(cv2, "setLogLevel", None)
        error_level = getattr(cv2, "LOG_LEVEL_ERROR", None)
        previous_level = None
        if callable(get_level) and callable(set_level) and error_level is not None:
            previous_level = get_level()
            set_level(error_level)
        try:
            for i in range(upper):
                cap = self._try_open(i)
                if cap is None:
                    continue
                try:
                    ret, _ = cap.read()
                    if ret:
                        self.available_cameras.append(i)
                finally:
                    cap.release()
        finally:
            if previous_level is not None:
                set_level(previous_level)
        logger.info(f"Available cameras: {self.available_cameras}")
        return self.available_cameras

    @staticmethod
    def _fourcc_name(cap) -> str:
        """The pixel format the device actually settled on, as four letters."""
        try:
            raw = int(cap.get(cv2.CAP_PROP_FOURCC))
        except Exception:  # noqa: BLE001 - a backend without the property
            return ""
        if raw <= 0:
            return ""
        return "".join(chr((raw >> (8 * i)) & 0xFF) for i in range(4)).strip("\x00 ")

    def _negotiate_format(self) -> str:
        """Ask the device for the preferred format and report what it gave.

        The fourcc has to be set *before* the frame size: the V4L2 backend
        applies the current pixel format when the size is applied, so setting
        the size first and the format second leaves the device running the
        format it opened with.
        """
        if self.cap is None:
            return ""
        try:
            self.cap.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*PREFERRED_FOURCC),
            )
        except Exception as e:  # noqa: BLE001 - a device may refuse the format
            logger.warning("Camera refused the %s format: %s", PREFERRED_FOURCC, e)
        return self._fourcc_name(self.cap)

    def start(self, camera_index: int = None, frame_callback: Callable[[np.ndarray], None] = None) -> bool:
        if self.is_running:
            self.stop()

        if camera_index is not None:
            self.camera_index = camera_index
        
        self.frame_callback = frame_callback

        try:
            self.cap = self._try_open(self.camera_index)
            if self.cap is None:
                logger.error(f"Failed to open camera {self.camera_index}")
                self.is_running = False
                return False

            # Format first, then size: the V4L2 backend latches the pixel
            # format when the frame size is applied.
            negotiated = self._negotiate_format()
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_FPS, self.fps)
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

            actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            actual_fps = int(self.cap.get(cv2.CAP_PROP_FPS))
            settled = self._fourcc_name(self.cap) or negotiated
            logger.info(
                f"Camera started: {actual_width}x{actual_height} @ {actual_fps}fps "
                f"({settled or 'unknown format'})"
            )
            if settled and settled.upper() != PREFERRED_FOURCC:
                logger.info(
                    "Camera is delivering %s rather than the preferred %s; the "
                    "delivered frame rate will be lower than it could be.",
                    settled, PREFERRED_FOURCC,
                )

            # A camera that cannot reach the requested rate is not an error, but
            # it does cap what the pipeline can do. The pipeline throttles to
            # SCAN_TARGET_FPS, so if the device delivers fewer frames than that
            # the throttle is never reached and inference is starved below its
            # target. Saying so here beats letting someone tune the pipeline
            # against a limit the hardware set.
            if actual_fps and actual_fps < settings.SCAN_TARGET_FPS:
                logger.warning(
                    "Camera %d requests %dfps, below the pipeline target of %dfps; "
                    "the detection rate will be limited by capture, not by inference.",
                    self.camera_index, actual_fps, settings.SCAN_TARGET_FPS,
                )

            self._measured_fps = 0.0
            self._frames_in_window = 0
            self._window_started_at = time.monotonic()
            self.is_running = True
            self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
            self.capture_thread.start()
            return True

        except Exception as e:
            logger.error(f"Camera start failed: {e}")
            self.is_running = False
            return False

    def _capture_loop(self):
        while self.is_running and self.cap and self.cap.isOpened():
            ret, frame = self.cap.read()
            if not ret:
                logger.warning("Failed to read frame from camera")
                time.sleep(0.01)
                continue

            with self.frame_lock:
                self.latest_frame = frame.copy()
                self._frame_id += 1

            self._record_frame_rate()

            if self.frame_callback:
                try:
                    self.frame_callback(frame)
                except Exception as e:
                    logger.error(f"Frame callback error: {e}")

            # No sleep here, deliberately. ``read()`` already blocks until the
            # device has a frame, so sleeping after it can only add latency:
            # at 30 fps it cost a third of the frame rate for nothing. The
            # pipeline does its own throttling to ``SCAN_TARGET_FPS`` by
            # skipping frame ids, so the capture thread is free to run as fast
            # as the hardware allows.

    def _record_frame_rate(self) -> None:
        """Update the measured capture rate over a one-second window."""
        self._frames_in_window += 1
        elapsed = time.monotonic() - self._window_started_at
        if elapsed < 1.0:
            return
        self._measured_fps = self._frames_in_window / elapsed
        self._frames_in_window = 0
        self._window_started_at = time.monotonic()

    def get_latest_frame(self) -> Optional[np.ndarray]:
        with self.frame_lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()
        return None

    def get_latest_frame_with_id(self) -> Tuple[Optional[np.ndarray], int]:
        """Return ``(frame, frame_id)`` for consumers that must not reprocess.

        ``frame_id`` increments on every captured frame, so a pipeline thread
        can skip images it has already analysed instead of burning inference on
        a frame the capture thread has already replaced. Returns ``(None, id)``
        when nothing has been captured yet.
        """
        with self.frame_lock:
            if self.latest_frame is None:
                return None, self._frame_id
            return self.latest_frame.copy(), self._frame_id

    def stop(self):
        self.is_running = False
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=2.0)
        if self.cap:
            self.cap.release()
            self.cap = None
        self.latest_frame = None
        self._measured_fps = 0.0
        self._frames_in_window = 0
        logger.info("Camera stopped")

    def get_camera_info(self) -> dict:
        if not self.cap or not self.cap.isOpened():
            return {"active": False}
        return {
            "active": True,
            "index": self.camera_index,
            "width": int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": int(self.cap.get(cv2.CAP_PROP_FPS)),
            # The rate the device was asked for, and the rate it actually
            # delivers. They are different numbers on this hardware.
            "format": self._fourcc_name(self.cap),
            "measured_fps": round(self._measured_fps, 2),
        }