"""Recover a CODE39 payload from a symbol whose bars no longer resolve.

Why this exists
---------------
A CODE39 symbol is not 175 independent bits.  It is a fixed number of
characters drawn from a 44-symbol alphabet -- roughly 58 bits of information
spread over 175 modules -- so a reader that knows the alphabet can recover a
symbol whose individual bars have stopped resolving, as long as the module
grid is right.  That is the only way past the information floor: at one pixel
per module the bars are already too blurred for a general-purpose reader, and
no amount of upscaling or sharpening brings information back.

Measurements that shaped the algorithm
--------------------------------------
* The column darkness profile of a CODE39 symbol repeats every **16 modules**
  (15 modules of character plus a one-module gap).  Its autocorrelation
  therefore peaks at exactly the character period, which is where the module
  width comes from -- it is *read off the data*, not searched blindly.
* How the symbol was blurred decides which character template fits.  A card
  whose bars were 3 pixels wide, shrunk to one pixel per module, is smeared
  over roughly three modules, and the template match only works with a blur
  kernel of about that width.  The blur is therefore searched over a small
  family of kernels rather than assumed.
* The module width has to be right to a fraction of a percent: over a
  175-module symbol a one-percent error is nearly two modules of drift, and
  the characters stop matching.  The search therefore resolves the module
  width finely, which is affordable only because every phase of one module
  width is evaluated at once.
* A grid's own per-character residual cannot rank grids: the predicted
  samples at a character's ends do not include the bleed from its
  neighbours, and that term is the same for every grid.  Scoring the
  *global* model -- one convolution of the reconstructed module sequence over
  the whole profile -- removes the term and separates good grids from bad.
* Most wrong grids do not produce two ``*`` characters, so a symbol run
  delimited by ``*`` is both a strong filter and a correctness requirement.
* A reconstruction has to explain the *whole* profile, not only the run it
  claims.  A run that stops in the middle of the symbol leaves real bars
  unexplained, and the leftover energy is what separates it from a good one.

Safety
------
A wrong payload is a real student's card attributed to the wrong person, so
this module never returns a guess.  It returns a payload only when the
reconstruction explains the observed profile to a residual below
:data:`RESIDUAL_LIMIT`, and the caller is expected to verify the payload
independently (the decoder re-renders the symbol and has a second reader
confirm it).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

try:  # pragma: no cover - the charset ships with python-barcode
    from barcode.charsets.code39 import CODES, EDGE, REF
except Exception:  # pragma: no cover - fall back to an empty alphabet
    CODES: Sequence[str] = ()
    EDGE = ""
    REF: Sequence[str] = ()

#: The 44 characters a CODE39 symbol may contain, ``*`` included.
NAMES: List[str] = list(REF) + ["*"]

#: The module pattern of each character: 9 elements, 3 wide and 6 narrow.
TEMPLATES = np.array(
    [[1.0 if c == "1" else 0.0 for c in pattern] for pattern in list(CODES) + [EDGE]],
    dtype=np.float64,
)

#: Index of the start/stop character in :data:`NAMES`.
STAR = NAMES.index("*")

#: Modules occupied by one character (five bars plus four spaces).
CHAR_MODULES = 15

#: Modules per character including the one-module gap that follows it.
CHAR_PERIOD = 16

#: Largest mean squared error per module sample a reconstruction may have.
#: Calibrated on the project's own cards: a correct reconstruction sits well
#: below 0.15, while a wrong one is an order of magnitude worse.
RESIDUAL_LIMIT = 0.20

#: Sub-pixel resolution of the integral table used to sample module cells.
FINE = 8

#: Phase steps searched over one character period.
PHASE_STEPS = 48

#: Module-width multipliers searched around the autocorrelation's estimate.
WIDTH_SPAN = 0.12

#: Step of the module-width ladder, as a fraction of the estimate.
WIDTH_STEP = 0.002

#: Narrowest module the reconstruction will consider.  Below roughly one
#: pixel per module there is no information left to recover.
MIN_MODULE_WIDTH = 0.8

#: How many grids are re-checked with every blur kernel.
CANDIDATE_LIMIT = 60

#: How many independent grids must agree on a payload before it is believed.
MIN_VOTES = 2

#: Longest symbol the reconstruction will consider, in characters.  A crop
#: that holds a card rather than a barcode implies a far longer "symbol", and
#: refusing to model it is what stops the reconstruction from inventing a
#: payload out of a photograph.
MAX_CHARACTERS = 24

#: How much of the inked area the recovered symbol has to cover.  A symbol
#: that fills the ink it sits on is a symbol; a short run inside a larger
#: pattern is a coincidence.
MIN_COVERAGE = 0.70
MAX_COVERAGE = 1.30

#: How much of the profile's variance the reconstruction has to account for.
#:
#: A low-error fit is not enough on its own: a profile with little structure
#: can be fitted almost perfectly by any module sequence, because the fitted
#: contrast collapses towards zero.  Measured on this project's own images,
#: a photograph of a card explains at most a third of its profile's variance,
#: while a real symbol explains two thirds or more.
MIN_EXPLAINED = 0.42


def _gaussian(sigma: float, taps: int = 4) -> np.ndarray:
    j = np.arange(-taps, taps + 1)
    k = np.exp(-(j ** 2) / (2.0 * sigma ** 2))
    return k / k.sum()


#: The blur family searched.  The first entry is the module cell average
#: convolved with a box of the same width; the rest are Gaussians of growing
#: width, which is how a real lens smears a narrow bar.
KERNELS: Tuple[Tuple[str, np.ndarray], ...] = (
    ("box", np.array([0.25, 0.50, 0.25])),
    ("g0.7", _gaussian(0.7)),
    ("g1.0", _gaussian(1.0)),
    ("g1.4", _gaussian(1.4)),
    ("g1.9", _gaussian(1.9)),
)


@dataclass
class Code39Recovery:
    """A reconstructed CODE39 payload and the evidence behind it."""

    #: The payload, without the leading and trailing ``*``.
    data: str

    #: Mean squared error per module sample between the observed profile and
    #: the reconstruction.  Lower is better; :data:`RESIDUAL_LIMIT` is the
    #: accept threshold.
    residual: float

    #: The module width, in pixels, that the recovery settled on.
    module_width: float

    #: The characters including the ``*`` delimiters.
    characters: str

    #: The number of characters the run spans, delimiters included.
    length: int

    #: Left edge of the recovered symbol, in the crop's pixels.
    left: int = 0

    #: Top edge of the recovered symbol, in the crop's pixels.
    top: int = 0

    #: Width of the recovered symbol, in the crop's pixels.
    width: int = 0

    #: Height of the recovered symbol, in the crop's pixels.
    height: int = 0

    #: The grid's origin: the pixel where the first module cell starts.
    phase: float = 0.0


# --------------------------------------------------------------- profile --


def darkness_profile(band: np.ndarray) -> Optional[np.ndarray]:
    """Column darkness of a symbol band, in [0, 1]; 1 means fully dark.

    The *median* over rows is used rather than the mean: a crop carries a
    margin above and below the symbol, and the median keeps the bars' own
    value as long as the symbol covers more than half the crop's height.  A
    mean would dilute the bars with whatever the margin contains.
    """
    if band is None or band.size == 0:
        return None
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    if gray.ndim != 2 or gray.shape[0] < 3 or gray.shape[1] < 16:
        return None
    profile = np.median(gray, axis=0).astype(np.float64)
    low, high = float(profile.min()), float(profile.max())
    if high - low < 4.0:
        return None
    return (high - profile) / (high - low)


def _cumulative(profile: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(profile)])


def _fine_cumulative(profile: np.ndarray, fine: int = FINE) -> np.ndarray:
    """The profile's integral on a sub-pixel grid.

    Sampling a module cell means integrating the profile over the cell, so
    the samples for *every* phase of one module width can be read off one
    table.  That is what makes a fine module-width search affordable.
    """
    width = profile.size
    cum = _cumulative(profile)
    grid = np.arange(width * fine + 1) / fine
    return np.interp(grid, np.arange(width + 1, dtype=float), cum)


def module_samples(cum: np.ndarray, width: int, m: float, x0: float) -> np.ndarray:
    """Mean darkness of every module cell that starts at ``x0``.

    A cell that hangs over the edge of the crop contributes what is visible
    and is still divided by the full cell width, so a partially visible module
    correctly reads as fainter rather than as a full one.
    """
    count = int((width - x0) // m)
    if count < CHAR_MODULES:
        return np.zeros(0)
    k = np.arange(count)
    low = np.clip(x0 + k * m, 0, width).astype(np.intp)
    high = np.clip(x0 + (k + 1) * m, 0, width).astype(np.intp)
    return (cum[high] - cum[low]) / m


# -------------------------------------------------------------- matching --


def apply_kernel(templates: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """Convolve each template with a symmetric module-space kernel."""
    n, m = templates.shape
    centre = kernel.size // 2
    out = np.zeros((n, m))
    for j in range(kernel.size):
        t = j - centre
        if t >= 0:
            out[:, t:] += kernel[j] * templates[:, : m - t]
        else:
            out[:, : m + t] += kernel[j] * templates[:, -t:]
    return out


def normalised_templates(kernel: np.ndarray) -> np.ndarray:
    """Character templates blurred by ``kernel`` and scaled to unit length.

    The templates are centred (so the card's background drops out) and
    normalised, which turns the match into a plain correlation: the best
    template for a position is the one with the largest squared cosine
    similarity, and the leftover energy is the residual.
    """
    predicted = apply_kernel(TEMPLATES, kernel)
    centred = predicted - predicted.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(centred, axis=1, keepdims=True)
    return centred / np.maximum(norm, 1e-12)


def match_characters(
    samples: np.ndarray, normalised: np.ndarray
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """Best template per character position, and its residual.

    The residual is the energy left after the best scale-and-offset fit of a
    template to what was observed, so a washed-out or offset profile is
    judged on its *shape* -- which is what survives downsampling.
    """
    n_char = (samples.size - CHAR_MODULES) // CHAR_PERIOD + 1
    if n_char < 1:
        return None, None
    padded = np.zeros(n_char * CHAR_PERIOD)
    take = min(samples.size, padded.size)
    padded[:take] = samples[:take]
    observed = padded.reshape(n_char, CHAR_PERIOD)[:, :CHAR_MODULES]
    o = observed - observed.mean(axis=1, keepdims=True)
    energy = (o * o).sum(axis=1)
    scores = (normalised @ o.T) ** 2
    picks = np.argmax(scores, axis=0)
    best = scores[picks, np.arange(n_char)]
    return picks, np.maximum(energy - best, 0.0)


def all_runs(picks: np.ndarray) -> List[Tuple[int, int]]:
    """Every run of characters delimited by two *consecutive* ``*``.

    A CODE39 symbol starts and stops with ``*`` and contains no other ``*``,
    so the only candidate runs are the gaps between neighbouring ``*``
    positions.  A reconstruction that does not produce two of them is not a
    symbol and is discarded.
    """
    stars = np.flatnonzero(picks == STAR)
    runs: List[Tuple[int, int]] = []
    for a, b in zip(stars[:-1], stars[1:]):
        if int(b - a) + 1 >= 3:
            runs.append((int(a), int(b)))
    return runs


def module_sequence(picks: np.ndarray, j0: int, j1: int, length: int) -> np.ndarray:
    """The module values of the recovered characters, aligned to the grid.

    Positions outside the recovered run stay zero: the symbol's own module
    values there are unknown, and inventing them would let the blur fit
    explain them away.
    """
    seq = np.zeros(length)
    for c in range(j0, j1 + 1):
        start = c * CHAR_PERIOD
        if start + CHAR_MODULES > length:
            break
        seq[start:start + CHAR_MODULES] = TEMPLATES[picks[c]]
    return seq


def run_residual(
    samples: np.ndarray,
    picks: np.ndarray,
    j0: int,
    j1: int,
    kernel: np.ndarray,
) -> float:
    """Mean squared error per module sample over the whole profile.

    The profile is compared with the model ``c + a * prediction``; the
    contrast and offset are free because the card's own brightness is not
    known, but nothing else is.  The blur kernel is *not* fitted: a fitted
    kernel can absorb the answer, which is how a wrong reconstruction ends
    up explaining the data perfectly.

    The whole profile is measured, not just the run, so a run that stops in
    the middle of the symbol leaves real bars unexplained and is penalised.
    """
    return run_fit(samples, picks, j0, j1, kernel)[0]


def run_fit(
    samples: np.ndarray,
    picks: np.ndarray,
    j0: int,
    j1: int,
    kernel: np.ndarray,
) -> Tuple[float, float]:
    """How well the reconstruction explains the profile.

    Returns the mean squared error per sample and the fraction of the
    profile's variance the model accounts for.

    The second number is the one that keeps the reconstruction honest.  A
    profile with little structure -- the smooth shading of a photograph, a
    blank wall -- can be fitted almost perfectly by *any* module sequence,
    because the fitted contrast simply collapses towards zero.  Such a fit
    has a low error and explains nothing.  Requiring the model to account
    for a real share of the variance is what tells a barcode from a
    background, and it is the check that stops a card photograph from being
    read as a three-character symbol.
    """
    sequence = module_sequence(picks, j0, j1, samples.size)
    taps = kernel.size // 2
    padded = np.concatenate([np.zeros(taps), sequence, np.zeros(taps)])
    pred = np.convolve(padded, kernel, mode="valid")[: samples.size]
    obs = samples
    n = obs.size
    sx = float(pred.sum())
    sy = float(obs.sum())
    sxx = float((pred * pred).sum())
    sxy = float((pred * obs).sum())
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return float("inf"), 0.0
    a = (n * sxy - sx * sy) / denom
    c = (sy - a * sx) / n
    r = obs - (c + a * pred)
    mse = float((r * r).sum() / max(1, n))
    centred = obs - obs.mean()
    total = float((centred * centred).sum())
    if total <= 1e-12:
        return mse, 0.0
    return mse, float(1.0 - (r * r).sum() / total)


def candidate_periods(profile: np.ndarray, limit: int = 3) -> List[int]:
    """Lags where the autocorrelation peaks; the character period is 16m."""
    p = profile - profile.mean()
    ac = np.correlate(p, p, mode="full")[p.size - 1:]
    if ac.size < 4 or ac[0] <= 0:
        return []
    ac = ac / ac[0]
    peaks: List[Tuple[float, int]] = []
    for i in range(2, ac.size - 2):
        if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1] and ac[i] > 0.05:
            peaks.append((float(ac[i]), i))
    peaks.sort(reverse=True)
    return [lag for _, lag in peaks[:limit]]


def symbol_extent(profile: np.ndarray, level: float = 0.15) -> Optional[Tuple[int, int]]:
    """The columns the symbol itself occupies.

    The profile is normalised so that 0 is the lightest column, which for a
    crop of a barcode is the card's background.  The symbol is therefore the
    contiguous stretch of columns that carry any ink at all, and its extent
    fixes how many characters the symbol has -- a fact about the *card*, not
    about any model, which is what keeps a wrong grid from claiming a
    thirty-character symbol.
    """
    ink = profile > level
    if not ink.any():
        return None
    start = int(np.argmax(ink))
    end = int(ink.size - np.argmax(ink[::-1]))
    if end - start < CHAR_MODULES:
        return None
    return start, end


def row_extent(band: np.ndarray, level: float = 0.15) -> Tuple[int, int]:
    """The rows the symbol occupies, in the crop's pixels."""
    gray = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY) if band.ndim == 3 else band
    if gray.ndim != 2 or gray.shape[0] < 3:
        return 0, int(band.shape[0])
    darkness = 255.0 - gray.astype(np.float64)
    rows = np.median(darkness, axis=1)
    peak = float(rows.max())
    if peak <= 0:
        return 0, int(band.shape[0])
    ink = rows > level * peak
    start = int(np.argmax(ink))
    end = int(ink.size - np.argmax(ink[::-1]))
    return start, end


def expected_characters(extent: Tuple[int, int], m: float) -> int:
    """How many characters a symbol of this extent and module width holds."""
    span = (extent[1] - extent[0]) / m
    return max(2, int(round((span + 1.0) / CHAR_PERIOD)))



# ------------------------------------------------------- vectorised scan --


def _phase_samples(
    cum_fine: np.ndarray, fine: int, width: int, m: float, phases: np.ndarray
) -> Optional[np.ndarray]:
    """Module samples for every phase at once; shape ``(phases, cells)``."""
    count = int((width - float(phases.min())) // m)
    if count < CHAR_MODULES:
        return None
    k = np.arange(count + 1)
    idx = np.rint((phases[:, None] + k[None, :] * m) * fine).astype(np.intp)
    np.clip(idx, 0, cum_fine.size - 1, out=idx)
    vals = cum_fine[idx]
    return np.diff(vals, axis=1) / m


def _match_all(
    samples: np.ndarray, normalised: np.ndarray
) -> Tuple[np.ndarray, np.ndarray]:
    """Best template per character position for every phase.

    Returns ``(picks, residual)`` with shapes ``(phases, characters)``.
    """
    n_phase, n_cell = samples.shape
    n_char = (n_cell - CHAR_MODULES) // CHAR_PERIOD + 1
    padded = np.zeros((n_phase, n_char * CHAR_PERIOD))
    take = min(n_cell, n_char * CHAR_PERIOD)
    padded[:, :take] = samples[:, :take]
    observed = padded.reshape(n_phase, n_char, CHAR_PERIOD)[:, :, :CHAR_MODULES]
    o = observed - observed.mean(axis=2, keepdims=True)
    energy = np.einsum("ptc,ptc->pt", o, o)
    scores = np.matmul(normalised, np.swapaxes(o, 1, 2)) ** 2
    picks = np.argmax(scores, axis=1)
    best = np.take_along_axis(scores, picks[:, None, :], axis=1)[:, 0, :]
    return picks, np.maximum(energy - best, 0.0)


def _runs_of(picks: np.ndarray) -> List[Tuple[int, int, int]]:
    """Every ``*``-delimited run, as ``(phase, first, last)``."""
    n_phase, n_char = picks.shape
    is_star = picks == STAR
    positions = np.arange(n_char)
    star_at = np.where(is_star, positions, n_char + 1)
    # The next ``*`` at or after each position, per phase.
    nxt = np.minimum.accumulate(star_at[:, ::-1], axis=1)[:, ::-1]
    runs: List[Tuple[int, int, int]] = []
    for p in range(n_phase):
        stars = np.flatnonzero(is_star[p])
        for a, b in zip(stars[:-1], stars[1:]):
            if int(b - a) + 1 >= 3:
                runs.append((p, int(a), int(b)))
    return runs


# ---------------------------------------------------------------- search --


def recover_code39(band: np.ndarray) -> Optional[Code39Recovery]:
    """Reconstruct the payload of a CODE39 symbol, or return ``None``.

    ``band`` is a crop that contains the symbol; a crop with a margin around
    it is fine and expected.
    """
    profile = darkness_profile(band)
    if profile is None:
        return None
    width = profile.size
    extent = symbol_extent(profile)
    if extent is None:
        return None
    cum = _cumulative(profile)
    cum_fine = _fine_cumulative(profile)

    # --- the module width comes from the autocorrelation -------------------
    periods = candidate_periods(profile)
    if not periods:
        return None

    banks = [(name, kernel, normalised_templates(kernel)) for name, kernel in KERNELS]
    probe = banks[0][2]

    # --- stage one: which grids could be a symbol at all? ------------------
    # A grid only matters if the characters it produces are delimited by two
    # ``*`` *and* are as many as the symbol's own extent calls for.  Almost
    # no wrong grid manages that, which is what makes the search safe to run
    # without a reference payload.
    candidates: List[Tuple[float, float]] = []
    seen_m = set()
    for lag in periods[:3]:
        m0 = lag / CHAR_PERIOD
        if m0 <= 0.2:
            continue
        for factor in np.arange(
            1.0 - WIDTH_SPAN, 1.0 + WIDTH_SPAN + 1e-9, WIDTH_STEP
        ):
            m = m0 * float(factor)
            if m < MIN_MODULE_WIDTH:
                continue
            key = round(m, 5)
            if key in seen_m:
                continue
            seen_m.add(key)
            want = expected_characters(extent, m)
            if want > MAX_CHARACTERS:
                # The crop holds something bigger than a barcode -- a card,
                # a photograph, a whole frame.  Modelling it as a symbol
                # would invent a payload out of whatever pattern the crop
                # happens to have, so the width is refused outright.
                continue
            phases = np.arange(PHASE_STEPS) * (CHAR_PERIOD * m / PHASE_STEPS)
            samples = _phase_samples(cum_fine, FINE, width, m, phases)
            if samples is None:
                continue
            picks, _ = _match_all(samples, probe)
            for phase, j0, j1 in _runs_of(picks):
                if abs((j1 - j0 + 1) - want) <= 1:
                    candidates.append((m, float(phases[phase])))
    if not candidates:
        return None

    # --- stage two: read the characters with each blur in the family -------
    # A payload is only accepted when *several* independent grids agree on
    # it.  A genuine symbol is found by every neighbouring module width and
    # phase that is close enough, while a fluke -- a grid that happens to
    # fit the noise -- is found once.  This is what keeps a near miss from
    # being mistaken for a student's number.
    votes: Dict[str, set] = {}
    best_for: Dict[str, Code39Recovery] = {}
    for m, phase in candidates:
        samples = module_samples(cum, width, m, phase)
        if samples.size < CHAR_MODULES * 3:
            continue
        want = expected_characters(extent, m)
        if want > MAX_CHARACTERS:
            continue
        inked = float(extent[1] - extent[0])
        for name, kernel, normalised in banks:
            picks, _ = match_characters(samples, normalised)
            if picks is None:
                continue
            for j0, j1 in all_runs(picks):
                length = j1 - j0 + 1
                if abs(length - want) > 1:
                    continue
                # The symbol has to fill the ink it sits on.  A run that
                # covers only a fraction of the inked area is a coincidence
                # inside somebody else's pattern, not a barcode.
                coverage = length * CHAR_PERIOD * m / max(1.0, inked)
                if not (MIN_COVERAGE <= coverage <= MAX_COVERAGE):
                    continue
                residual, explained = run_fit(samples, picks, j0, j1, kernel)
                if residual > RESIDUAL_LIMIT:
                    continue
                if explained < MIN_EXPLAINED:
                    # The fit is good but hollow: the reconstruction is not
                    # what makes this profile look the way it does.
                    continue
                characters = "".join(NAMES[picks[c]] for c in range(j0, j1 + 1))
                if not (characters.startswith("*") and characters.endswith("*")):
                    continue
                payload = characters[1:-1]
                if not payload:
                    continue
                candidate = Code39Recovery(
                    payload, residual, m, characters, length, phase=phase
                )
                votes.setdefault(payload, set()).add((round(m, 4), round(phase, 3)))
                current = best_for.get(payload)
                if current is None or candidate.residual < current.residual:
                    best_for[payload] = candidate
    if not votes:
        return None
    payload = max(
        votes,
        key=lambda p: (len(votes[p]), -best_for[p].residual),
    )
    if len(votes[payload]) < MIN_VOTES:
        return None
    winner = best_for[payload]
    # Where the recovered symbol sits in the crop, so a caller can still
    # draw a box around it.
    winner.left = int(round(winner.phase))
    winner.width = int(round(winner.length * CHAR_PERIOD * winner.module_width))
    top, bottom = row_extent(band)
    winner.top = top
    winner.height = max(1, bottom - top)
    return winner

