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

GOLDEN_PROSE_MEMBERS = ("report.md", "report.html", "self_claims.json")


def test_every_llm_instruction_block_pins_the_owner_reference_form() -> None:
    for name, block in INSTRUCTION_BLOCKS.items():
        assert "§16.14" in block, name
        assert "second person" in block, name
        assert "third person" in block, name
        assert "first person" in block or "first or third person" in block, name
        assert "by name" in block or "by personal name" in block, name


def test_the_verifier_licenses_the_form_it_judges_candidates_against() -> None:
    """§15.1 rule 11: the verifier states §16.14's licensed half, not the ban alone.

    Issue #219: stating §16.14 to the verifier only as a violation inverted
    §13.7 check 12 against the §15.4 writer contract, and four live Stage 7
    runs rejected the second-person prose §16.14 mandates. The pins below
    are on the current licensing wording, not on §16.14 itself — a rewrite
    that keeps both halves adjusts them.
    """

    block = ASSESSMENT_VERIFIER_INSTRUCTIONS
    licensed, _, forbidden = block.partition("The violation is")
    assert forbidden, "the verifier no longer names the §16.14 violation"
    # The licensed half comes first and covers the candidate's own prose,
    # so no reading reaches the prohibition without it.
    assert "licenses exactly two owner-referential forms" in licensed
    assert "subject-free" in licensed
    assert "Candidate prose that addresses the owner as you" in licensed
    for consequence in (
        "never quote it as an unsupported phrase",
        "never ground counterevidence on it",
        "never lower a status for it",
    ):
        assert consequence in licensed, consequence


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
