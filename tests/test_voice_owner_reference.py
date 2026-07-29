"""Offline §16.14 owner-reference scans: instruction pins and golden prose."""

from __future__ import annotations

import re

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

# §16.14's example role nouns as word-bounded singular tokens, so a legal
# non-owner referent such as "the users" of shipped software passes. This is
# a pin on how the current goldens are constructed, not the rule itself —
# §16.14 is semantic and open-ended, and a future golden that legitimately
# needs one of these tokens for a non-owner referent adjusts this pin
# alongside that golden.
FORBIDDEN_OWNER_NOUN_PATTERNS = tuple(
    re.compile(rf"\bthe {noun}\b")
    for noun in ("user", "subject", "author", "owner", "developer", "candidate")
)

GOLDEN_PROSE_MEMBERS = ("report.md", "self_claims.json")


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
        for pattern in FORBIDDEN_OWNER_NOUN_PATTERNS:
            assert not pattern.search(lowered), (member, pattern.pattern)
        # §16.14 forbids the owner's personal name in generated prose, so the
        # prose goldens are marker-exempt (scripts/check_public_hygiene.py);
        # fixture lineage lives in the vera entity IDs instead.
        assert "vera example" not in lowered, member
