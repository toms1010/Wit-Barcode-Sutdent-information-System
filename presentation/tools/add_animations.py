#!/usr/bin/env python3
"""Add real PowerPoint transitions and animations to the generated deck.

``python-pptx`` 1.0.2 exposes no API for either transitions or animations, so
this module writes the OOXML directly into each slide part:

* **Transitions** -- a ``<p:transition>`` element on the slide: ``fade``
  everywhere, with a distinct effect (cover / push) at each section boundary.
* **Animations** -- a ``<p:timing>`` tree whose behaviour nodes target real
  shape ids, plus a ``<p:bldLst>`` paragraph build so multi-bullet text boxes
  reveal one bullet at a time instead of all at once.

Motion roles
------------
==================  ===========================  ================================
role                effect                       applies to
==================  ===========================  ================================
title               Fade                         SlideTitle, Kicker, DeckTitle
subtitle            Fade                         Subtitle, Caption, SectionTag
metric              Zoom (fade + scale)          KPI values, config tiles
image               Zoom (fade + scale)          screenshots, chart frames
node                Wipe (left-to-right)         flow / architecture nodes
chip                Appear                       layer chips, agenda numbers
bullet              Appear, by paragraph         body bullet text frames
fade                Fade                         anything unclassified
==================  ===========================  ================================

Sequencing
----------
A beat is one audience click. Everything in a beat starts together after a
short stagger, so a title beats in, then its supporting content beats in -- not
forty individual clicks. A slide is capped at ``MAX_BEATS`` advance points
(see :func:`plan_beats`), which keeps a talk moving at speaking pace.

Everything emitted is standard ``p:`` / ``a:`` DrawingML: the effects play in
PowerPoint Slide Show and stay editable in the Animation Pane. Nothing is
flattened into an image.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn

PRES = Path(__file__).resolve().parent.parent
OUT = PRES / "WIT_Barcode_Student_Information_System_Technical_Presentation_Revised.pptx"

# --------------------------------------------------------------------------- #
# timing (milliseconds, as PowerPoint stores them)
# --------------------------------------------------------------------------- #
FADE_DUR = 500
APPEAR_DUR = 300
WIPE_DUR = 450
ZOOM_DUR = 450
TRANSITION_DUR = 600
TRANSITION_DUR_FAST = 500
STAGGER = 120          # offset between shapes inside one beat

#: Hard cap on audience clicks per slide (one click per beat).
MAX_BEATS = 4

#: Distinct opening effect for each section change.
SECTIONS: Dict[int, Tuple[str, int]] = {
    1: ("fade", TRANSITION_DUR),
    3: ("cover", TRANSITION_DUR),      # Executive summary
    6: ("push", TRANSITION_DUR),       # System design
    12: ("cover", TRANSITION_DUR),     # Product walkthrough
    27: ("push", TRANSITION_DUR),      # Data & model
    37: ("cover", TRANSITION_DUR),     # Verification & roadmap
    39: ("fade", TRANSITION_DUR),      # Appendix
}

# --------------------------------------------------------------------------- #
# role classification
# --------------------------------------------------------------------------- #
#: Slide chrome: on screen before the slide animates. Never consumed in the
#: reveal order and never given an effect.
CHROME = {
    "TopBand", "FooterRule", "FooterText", "PageNumber", "Spine",
    "GoldRule", "KickerTick", "Rule",
}

#: (name token, role) -- first match wins.
ROLE_RULES: List[Tuple[str, str]] = [
    ("SlideTitle", "title"),
    ("DeckTitle", "title"),
    ("Kicker", "title"),
    ("SectionTitle", "title"),
    ("Subtitle", "subtitle"),
    ("SectionTag", "subtitle"),
    ("Caption", "subtitle"),
    ("Footnote", "subtitle"),
    ("TierSub", "subtitle"),
    ("DFProcSub", "subtitle"),
    ("DFStoreSub", "subtitle"),
    ("ExternalSub", "subtitle"),
    ("TechRole", "subtitle"),
    ("KPIValue", "metric"),
    ("KPIAccent", "metric"),
    ("KPILabel", "metric"),
    ("KPINote", "metric"),
    ("KPI", "metric"),
    ("CfgValue", "metric"),
    ("Image", "image"),
    ("Picture", "image"),
    ("Chart", "image"),
    ("Screenshot", "image"),
    ("MapChart", "image"),
    ("FlowArrow", "node"),
    ("Arrow", "node"),
    ("Conn", "node"),
    ("DFProc", "node"),
    ("DFStore", "node"),
    ("DFStoD", "node"),
    ("External", "node"),
    ("Tier", "node"),
    ("Train", "node"),
    ("Fit", "node"),
    ("Eval", "node"),
    ("Promote", "node"),
    ("Stage", "node"),
    ("Module", "node"),
    ("Entity", "node"),
    ("Card", "node"),
    ("Panel", "node"),
    ("TechPanel", "node"),
    ("TechAccent", "node"),
    ("TechTitle", "node"),
    ("TechName", "node"),
    ("FailState", "node"),
    ("Verify", "node"),
    ("State", "node"),
    ("Chip", "chip"),
    ("Tile", "chip"),
    ("CfgTile", "chip"),
    ("AgendaNum", "chip"),
    ("AgendaTitle", "bullet"),
    ("AgendaDesc", "bullet"),
    ("Bullet", "bullet"),
    ("Body", "bullet"),
    ("Item", "bullet"),
    ("Stat", "bullet"),
]

#: role -> (effect kind, duration)
ROLE_EFFECT: Dict[str, Tuple[str, int]] = {
    "title": ("fade", FADE_DUR),
    "subtitle": ("fade", FADE_DUR),
    "metric": ("zoom", ZOOM_DUR),
    "image": ("zoom", ZOOM_DUR),
    "node": ("wipe", WIPE_DUR),
    "chip": ("appear", APPEAR_DUR),
    "bullet": ("appear", APPEAR_DUR),
    "fade": ("fade", FADE_DUR),
}

#: Reveal order within a slide (lower runs first).
ORDER = {"title": 0, "subtitle": 1, "bullet": 2, "node": 3, "chip": 4,
         "metric": 5, "image": 6, "fade": 7}


def role_for(name: str) -> str:
    for token, role in ROLE_RULES:
        if token in name:
            return role
    return "fade"


def _role_of(shape) -> str:
    """Role for a shape, using name tokens first and paragraph count as a fallback.

    The deck's helper functions name most shapes ('SlideTitle', 'Chip',
    'DFProc'), but the bullet helper returns anonymous 'TextBox N' boxes. A
    multi-paragraph text box that no rule claimed is a bullet list, so it is
    classified as one -- that is what makes paragraph-level builds possible.
    """
    role = role_for(shape.name)
    if role == "fade" and shape.has_text_frame:
        n_par = len([p for p in shape.text_frame.paragraphs if p.text.strip()])
        if n_par > 1:
            return "bullet"
    return role


# --------------------------------------------------------------------------- #
# OOXML helpers
# --------------------------------------------------------------------------- #
NSMAP = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
}


def _clark(tag: str) -> str:
    prefix, _, local = tag.partition(":")
    if prefix in NSMAP:
        return "{%s}%s" % (NSMAP[prefix], local)
    return tag


def _el(tag: str, **attrs: str) -> etree._Element:
    e = etree.Element(_clark(tag), nsmap=NSMAP)
    for k, v in attrs.items():
        e.set(k, v)
    return e


def _sub(parent: etree._Element, tag: str, **attrs: str) -> etree._Element:
    e = etree.SubElement(parent, _clark(tag))
    for k, v in attrs.items():
        e.set(k, v)
    return e


# --------------------------------------------------------------------------- #
# transition
# --------------------------------------------------------------------------- #
_TRANS_TAG = {"fade": "fade", "cover": "cover", "push": "push", "wipe": "wipe"}


def _transition(effect: str, duration: int) -> etree._Element:
    tr = _el("p:transition", spd="med", advClick="1", dur=str(duration))
    tag = _TRANS_TAG.get(effect, "fade")
    el = _sub(tr, tag)
    if tag == "fade":
        el.set("thruBlk", "1")
    return tr


# --------------------------------------------------------------------------- #
# animation
# --------------------------------------------------------------------------- #
def _behaviour(bcl: etree._Element, spid: int, kind: str, duration: int,
               uid: int) -> None:
    """Append the animated behaviour for one shape into ``bcl``."""
    if kind in ("fade", "wipe", "zoom"):
        filt = {"fade": "fade", "wipe": "wipe(left)", "zoom": "fade"}[kind]
        anim = _sub(bcl, "p:animEffect", transition="in", filter=filt)
        cbe = _sub(anim, "p:cBhvr")
        _sub(cbe, "p:cTn", id=str(uid), dur=str(duration))
        tgt = _sub(cbe, "p:tgtEl")
        _sub(tgt, "p:spTgt", spid=str(spid))
        _sub(anim, "p:progress")
        if kind == "zoom":
            asc = _sub(bcl, "p:animScale")
            acb = _sub(asc, "p:cBhvr")
            _sub(acb, "p:cTn", id=str(uid + 1), dur=str(duration))
            t2 = _sub(acb, "p:tgtEl")
            _sub(t2, "p:spTgt", spid=str(spid))
            _sub(asc, "p:from")
            to = _sub(asc, "p:to")
            _sub(to, "p:by", x="20000", y="20000")
    else:  # appear
        anim = _sub(bcl, "p:set")
        cbe = _sub(anim, "p:cBhvr")
        _sub(cbe, "p:cTn", id=str(uid), dur="1", fill="hold")
        tgt = _sub(cbe, "p:tgtEl")
        _sub(tgt, "p:spTgt", spid=str(spid))
        attr = _sub(cbe, "p:attrNameLst")
        an = _el("p:attrName")
        an.text = "style.visibility"
        attr.append(an)
        to = _sub(anim, "p:to")
        _sub(to, "p:strVal", val="visible")


def _timing(beats: List[List[Tuple[int, str, int, int]]],
            paragraphs: Dict[int, int]) -> etree._Element:
    """Build the full <p:timing> element.

    ``beats``      -- one list per audience click; each entry is
                      (shape_id, kind, duration, delay_ms)
    ``paragraphs`` -- shape_id -> number of paragraphs to build one at a time
    """
    timing = _el("p:timing")
    tnLst = _sub(timing, "p:tnLst")
    seq = _sub(tnLst, "p:seq", concurrent="1", nextAc="seek")
    main = _sub(seq, "p:cTn", id="1", dur="indefinite",
                restart="whenNotActive", nodeType="mainSeq")
    child = _sub(main, "p:childTnLst")

    uid = 100
    for bi, beat in enumerate(beats, start=1):
        # ---- one click group per beat ------------------------------------- #
        par = _el("p:par")
        ctn = _sub(par, "p:cTn", id=str(uid), fill="hold",
                   nodeType="clickEffect")
        uid += 1
        st = _sub(ctn, "p:stCondLst")
        _sub(st, "p:cond", delay="0")
        itl = _sub(ctn, "p:childTnLst")

        # every effect in this beat shares the click, offset by its stagger
        for (spid, kind, dur, delay) in beat:
            epar = _sub(itl, "p:par")
            etn = _sub(epar, "p:cTn", id=str(uid), fill="hold")
            uid += 1
            est = _sub(etn, "p:stCondLst")
            _sub(est, "p:cond", delay=str(delay))
            echild = _sub(etn, "p:childTnLst")

            # visibility set
            sp = _sub(echild, "p:par")
            stn = _sub(sp, "p:cTn", id=str(uid), fill="hold")
            uid += 1
            sst = _sub(stn, "p:stCondLst")
            _sub(sst, "p:cond", delay="0")
            _sub(stn, "p:childTnLst")

            # behaviour
            bp = _sub(echild, "p:par")
            btn = _sub(bp, "p:cTn", id=str(uid), fill="hold", dur=str(dur))
            uid += 1
            bst = _sub(btn, "p:stCondLst")
            _sub(bst, "p:cond", delay="0")
            bcl = _sub(btn, "p:childTnLst")
            _behaviour(bcl, spid, kind, dur, uid)
            uid += 4

        child.append(par)

    _sub(seq, "p:prevCondLst")
    nxt = _sub(seq, "p:nextCondLst")
    _sub(nxt, "p:cond", evt="onNext", delay="0")

    # ---- paragraph build list: reveal bullets one at a time ---------------- #
    # p:bldLst holds one p:bldP per animated shape. A shape built "by
    # paragraph" is declared once with build="p" and group id, and is followed
    # by one p:bldP sibling per paragraph (same spid/grpId). Nesting p:bldP
    # inside p:bldP is invalid and PowerPoint drops the build silently.
    bldLst = _sub(timing, "p:bldLst")
    for spid, n_par in sorted(paragraphs.items()):
        if n_par > 1:
            _sub(bldLst, "p:bldP", spid=str(spid), grpId="0", build="p")
            for _ in range(n_par):
                _sub(bldLst, "p:bldP", spid=str(spid), grpId="0", build="p")
        else:
            _sub(bldLst, "p:bldP", spid=str(spid), grpId="0", build="allAtOnce")
    return timing


# --------------------------------------------------------------------------- #
# beat planning
# --------------------------------------------------------------------------- #
def _animatable(slide) -> List[Tuple[int, str, str, int]]:
    """(shape_id, name, role, paragraph_count) for shapes that get an effect."""
    out = []
    for sh in slide.shapes:
        if sh.name in CHROME:
            continue
        role = _role_of(sh)
        n_par = 0
        if sh.has_text_frame:
            n_par = len([p for p in sh.text_frame.paragraphs if p.text.strip()])
        out.append((sh.shape_id, sh.name, role, n_par))
    return out


def plan_beats(shapes: List[Tuple[int, str, str, int]]) -> List[Tuple[int, int, int]]:
    """Group shapes into at most ``MAX_BEATS`` sequential beats.

    Returns a list of (shape_index, beat, stagger_index). Roles are ordered
    first so the reveal always follows the reading order, then the ordered list
    is split into equal beats.
    """
    ordered = sorted(range(len(shapes)), key=lambda i: (ORDER.get(shapes[i][2], 9), i))
    n = len(ordered)
    if n == 0:
        return []
    beats = min(MAX_BEATS, max(1, n))
    out = []
    for pos, idx in enumerate(ordered):
        beat = min(beats - 1, pos * beats // n)
        out.append((idx, beat, pos))
    return out


# --------------------------------------------------------------------------- #
# assembly
# --------------------------------------------------------------------------- #
def build_animations(path: Path, verbose: bool = True) -> Dict[str, int]:
    prs = Presentation(str(path))
    stats: Dict[str, int] = {"beats": 0, "paragraph_builds": 0}
    max_beats = 0

    for idx, slide in enumerate(prs.slides, start=1):
        sld = slide._element

        # idempotent: drop any transition/timing from a previous run
        for tag in ("p:transition", "p:timing"):
            for old in sld.findall(qn(tag)):
                sld.remove(old)

        effect, dur = SECTIONS.get(idx, ("fade", TRANSITION_DUR_FAST))
        sld.append(_transition(effect, dur))

        shapes = _animatable(slide)
        plan = plan_beats(shapes)

        # group planned shapes into beats; each beat is one audience click
        n_beats = (max(b for _, b, _ in plan) + 1) if plan else 0
        beats: List[List[Tuple[int, str, int, int]]] = [[] for _ in range(n_beats)]
        paragraphs: Dict[int, int] = {}
        stagger_in_beat: Dict[int, int] = {}

        for shape_i, beat, _pos in plan:
            sid, name, role, n_par = shapes[shape_i]
            kind, d = ROLE_EFFECT.get(role, ("fade", FADE_DUR))
            k = stagger_in_beat.get(beat, 0)
            stagger_in_beat[beat] = k + 1
            beats[beat].append((sid, kind, d, k * STAGGER))
            stats[kind] = stats.get(kind, 0) + 1
            if role == "bullet" and n_par > 1:
                paragraphs[sid] = n_par
                stats["paragraph_builds"] += 1

        if beats:
            sld.append(_timing(beats, paragraphs))
            stats["beats"] += n_beats
            max_beats = max(max_beats, n_beats)

    prs.save(str(path))
    if verbose:
        print(f"[anim] transitions on {len(prs.slides)} slides")
        print(f"[anim] max beats on any slide: {max_beats}")
        print(f"[anim] paragraph-level bullet builds: {stats['paragraph_builds']}")
        for k in ("fade", "appear", "wipe", "zoom"):
            print(f"[anim]   {k:7s} {stats.get(k, 0)}")
    return stats


def main() -> None:
    build_animations(OUT)


if __name__ == "__main__":
    main()
