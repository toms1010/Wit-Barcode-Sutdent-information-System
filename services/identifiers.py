"""How a student is named on paper versus in the database.

A reference number reaches this application from three places -- the barcode on
a card, a reference typed into a search box, and a number printed on an
information panel -- and the three do not always agree. This module holds the
translation, so every screen and every service resolves a reference the same way
instead of each growing its own guess.

What it deliberately does **not** do is correct the stored record. ``000001`` is
an alias for the student whose reference is ``65902``; the panel that printed it
is a paperwork defect for the registrar to reissue, and rewriting
``students.reference_number`` to match would make the mapping ambiguous in the
other direction. See ``reports/ADMIN_ACTIONS_REQUIRED.md``.

Qt-free by design: services resolve identifiers without importing the interface.
"""

from __future__ import annotations

from typing import Optional

#: Printed references that identify a student but are not their stored reference.
#: Keys are compared as written, so ``"000001"`` and ``"1"`` are different keys.
#:
#: The zero-padded payloads of ordinary students are deliberately **not** listed
#: here. They are not arbitrary aliases: they are this project's own encoding of
#: a reference, and :func:`resolve_reference` derives them from
#: :data:`PAYLOAD_WIDTH` instead. Listing them by hand meant every new student
#: needed an entry added here or a lookup by their scanned payload reported
#: "no student on file"; the derivation has no such gap.
REFERENCE_ALIASES = {
    "000001": "65902",
}

#: How long a barcode payload is padded to in this project.
PAYLOAD_WIDTH = 9


def normalise_reference(reference) -> str:
    """Trim a reference to its stored form, keeping leading zeros.

    ``str`` rather than ``int``: the value is an identifier, and ``int("000001")``
    is ``"1"``, which matches no record.
    """
    return str(reference or "").strip()


def reference_from_payload(payload) -> str:
    """The stored reference a zero-padded barcode payload encodes.

    ``000065736`` -> ``65736``. Returns the input unchanged when it is not a
    padded payload, so this is safe to call on anything a scanner or a panel can
    print.
    """
    text = normalise_reference(payload)
    if (
        len(text) == PAYLOAD_WIDTH
        and text.isdigit()
        and text.startswith("0")
    ):
        return text.lstrip("0") or "0"
    return text


def resolve_reference(reference) -> str:
    """The stored ``reference_number`` a printed or scanned value refers to.

    Two rules, in order: a printed alias from :data:`REFERENCE_ALIASES`, then the
    project's own zero-padding convention. The second rule is why
    ``000065736`` -- what Steven Oquiana's card decodes to -- resolves to the
    reference on file, ``65736``, without anyone having to add his payload to a
    table by hand.
    """
    text = normalise_reference(reference)
    if not text:
        return ""
    aliased = REFERENCE_ALIASES.get(text)
    if aliased:
        return aliased
    return reference_from_payload(text)


def is_alias(reference) -> bool:
    """Whether the value is a printed alias rather than a stored reference."""
    text = normalise_reference(reference)
    return bool(text) and text in REFERENCE_ALIASES


def padded_payload(reference) -> str:
    """The barcode payload this project would encode for ``reference``."""
    text = resolve_reference(reference)
    return text.zfill(PAYLOAD_WIDTH) if text else ""


def matches_reference(student, reference) -> bool:
    """Whether ``student`` is the one ``reference`` refers to.

    Compares the stored reference, the padded payload and any printed alias, so a
    card scanned as ``000065902`` and a panel printed as ``000001`` both land on
    the same student without either being treated as a new one.
    """
    if student is None:
        return False
    wanted = normalise_reference(reference)
    if not wanted:
        return False

    stored = normalise_reference(getattr(student, "reference_number", ""))
    payload = normalise_reference(getattr(student, "barcode_payload", ""))

    # The student answers to their own identifiers -- the stored reference, the
    # payload on their card, and the padded form of the reference. ``wanted`` is
    # deliberately *not* added to that set: doing so would make any reference
    # match any student.
    if wanted in {stored, payload, padded_payload(stored)}:
        return True
    # A printed alias matches when it resolves to one of those.
    if resolve_reference(wanted) in {stored, payload, padded_payload(stored)}:
        return True
    # Zero-padded forms compare equal once the padding is ignored, which is this
    # project's encoding convention.
    numeric = {
        value.lstrip("0") or "0"
        for value in (wanted, stored, payload)
        if value.isdigit()
    }
    return len(numeric) == 1


def find_student_by_reference(students, reference) -> Optional[object]:
    """Find one student by any reference form, from an already-loaded iterable.

    A database query for the exact string would miss the aliases, which is how a
    student on file ends up reported as "not found" while their panel is in the
    operator's hand.
    """
    wanted = normalise_reference(reference)
    if not wanted:
        return None
    resolved = resolve_reference(wanted)
    for student in students or ():
        if matches_reference(student, resolved):
            return student
    return None


__all__ = [
    "PAYLOAD_WIDTH",
    "REFERENCE_ALIASES",
    "find_student_by_reference",
    "is_alias",
    "matches_reference",
    "normalise_reference",
    "padded_payload",
    "reference_from_payload",
    "resolve_reference",
]