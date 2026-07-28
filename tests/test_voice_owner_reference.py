"""Offline §16.14 owner-reference scans: instruction pins and golden prose."""

from __future__ import annotations

import pytest

from exp2res.llm.assessment_verifier import ASSESSMENT_VERIFIER_INSTRUCTIONS
from exp2res.llm.assessment_writer import ASSESSMENT_WRITER_INSTRUCTIONS
from exp2res.llm.detector import DETECTOR_INSTRUCTIONS
from exp2res.llm.fact_extractor import FACT_EXTRACTOR_INSTRUCTIONS
from exp2res.llm.signal_extractor import SIGNAL_EXTRACTOR_INSTRUCTIONS

from conftest import REPOSITORY_ROOT


pytestmark = [pytest.mark.unit]

INSTRUCTION_BLOCKS = {
    "fact-extractor": FACT_EXTRACTOR_INSTRUCTIONS,
    "gap-contradiction-detector": DETECTOR_INSTRUCTIONS,
    "self-signal-extractor": SIGNAL_EXTRACTOR_INSTRUCTIONS,
    "self-assessment-writer": ASSESSMENT_WRITER_INSTRUCTIONS,
    "assessment-verifier": ASSESSMENT_VERIFIER_INSTRUCTIONS,
}

# §16.14's example role nouns. The rule itself is open-ended and semantic;
# this closed list only guards the offline goldens and instruction text.
FORBIDDEN_OWNER_NOUNS = (
    "the user",
    "the subject",
    "the author",
    "the owner",
    "the developer",
    "the candidate",
)

GOLDEN_PROSE_MEMBERS = ("report.md", "self_claims.json")

# The one phrase that may carry the public-hygiene marker inside golden
# generated prose: the corpus label, never the owner as sentence actor.
MARKER = "Vera Example"
MARKER_LABEL_PHRASES = ("the supplied Vera Example records",)


def test_every_llm_instruction_block_pins_the_owner_reference_form() -> None:
    for name, block in INSTRUCTION_BLOCKS.items():
        assert "§16.14" in block, name
        assert "second person" in block, name
        assert "by name" in block, name


def test_generated_prose_in_goldens_carries_no_third_person_owner_nouns() -> None:
    goldens = REPOSITORY_ROOT / "tests" / "goldens" / "assessment"
    for member in GOLDEN_PROSE_MEMBERS:
        text = (goldens / member).read_text(encoding="utf-8")
        lowered = text.lower()
        for noun in FORBIDDEN_OWNER_NOUNS:
            assert noun not in lowered, (member, noun)
        # The hygiene marker stays present, but only as a record label.
        occurrences = text.count(MARKER)
        assert occurrences > 0, member
        labeled = sum(text.count(phrase) for phrase in MARKER_LABEL_PHRASES)
        assert occurrences == labeled, member
