"""Offline §16.14 and §15.1 rule 11 scans: instruction pins and golden prose."""

from __future__ import annotations

import importlib.util
import json
import re
import sys

import pytest

from exp2res.llm.assessment_verifier import ASSESSMENT_VERIFIER_INSTRUCTIONS
from exp2res.llm.assessment_writer import ASSESSMENT_WRITER_INSTRUCTIONS
from exp2res.llm.detector import DETECTOR_INSTRUCTIONS
from exp2res.llm.fact_extractor import FACT_EXTRACTOR_INSTRUCTIONS
from exp2res.llm.jd_parser import JD_PARSER_INSTRUCTIONS

from conftest import REPOSITORY_ROOT


pytestmark = [pytest.mark.unit]

INSTRUCTION_BLOCKS = {
    "fact-extractor": FACT_EXTRACTOR_INSTRUCTIONS,
    "gap-contradiction-detector": DETECTOR_INSTRUCTIONS,
    "self-assessment-writer": ASSESSMENT_WRITER_INSTRUCTIONS,
    "assessment-verifier": ASSESSMENT_VERIFIER_INSTRUCTIONS,
    "job-description-parser": JD_PARSER_INSTRUCTIONS,
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

CORPUS_PROSE_DIRECTORY = REPOSITORY_ROOT / "examples" / "vera" / "corpus" / "llm"


def load_module(name: str, relative: str):
    """Import a repository script that is not on the package path."""

    spec = importlib.util.spec_from_file_location(name, REPOSITORY_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def string_leaves(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [leaf for item in value.values() for leaf in string_leaves(item)]
    if isinstance(value, list):
        return [leaf for item in value for leaf in string_leaves(item)]
    return []

# §15.1 rule 11: every §16 rule a block encodes carries its licensed form
# beside the violation. One row per (block, rule): the licensed fragment
# first, then the forbidden one. Wording pins, not the rules themselves —
# a rewrite that keeps both halves adjusts the fragments.
TWO_HALF_PINS = (
    (
        "assessment-verifier",
        "§16.2",
        "allowed to be uncomfortable",
        "motivational fiction",
    ),
    (
        "assessment-verifier",
        "§16.4",
        "at or below the strongest level the linked evidence explicitly supports",
        "ownership above explicit support",
    ),
    (
        "assessment-verifier",
        "§16.7",
        "no narrower and no more exact than the strongest precision",
        "an interval that does not contain the evidence's own",
    ),
    (
        "assessment-verifier",
        "§16.8",
        "Employment framing is licensed only",
        "as employment",
    ),
    (
        "assessment-verifier",
        "§16.9",
        "bounded to what current evidence suggests",
        "identity",
    ),
    (
        "assessment-verifier",
        "§16.10",
        "is licensed mirror prose",
        "clinical label",
    ),
    (
        "self-assessment-writer",
        "§16.3",
        "A capability the evidence supports is stated plainly",
        "flattering terms without evidence",
    ),
    (
        "self-assessment-writer",
        "§16.9",
        "Current evidence suggests",
        "permanent-identity phrasing",
    ),
    (
        "self-assessment-writer",
        "§16.10",
        "is licensed mirror prose",
        "clinical labels",
    ),
    (
        "gap-contradiction-detector",
        "§16.9",
        "current evidence suggests",
        "permanent trait",
    ),
    (
        "job-description-parser",
        "§16.12",
        "Requirement text states what the vacancy demands",
        "never asserts, denies, or rates that anyone meets that demand",
    ),
    (
        "job-description-parser",
        "§16.3",
        "Demand wording is preserved faithfully",
        "never soften, inflate, or reinterpret",
    ),
    (
        "fact-extractor",
        "§16.4",
        "at or below the strongest level the evidence that fact selects",
        "never raises the ceiling",
    ),
    (
        "fact-extractor",
        "§16.8",
        "only as a record among that same fact's selected evidence states it",
        "never render an independent project, a competition, or learning",
    ),
)


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
    assert "Addressing the owner as you" in licensed
    for consequence in (
        "never an unsupported phrase",
        "never a counterevidence ground",
        "never a reason to lower a status",
    ):
        assert consequence in licensed, consequence
    # PR #226 review: the licence covers the grammatical form only, so it
    # cannot be read as an exemption for what the same prose asserts.
    assert "This exempts the form only" in licensed


@pytest.mark.parametrize(
    ("block_name", "rule", "licensed", "forbidden"), TWO_HALF_PINS
)
def test_each_encoded_rule_carries_its_licensed_half(
    block_name: str, rule: str, licensed: str, forbidden: str
) -> None:
    """§15.1 rule 11: no §16 rule is encoded by its violation alone (#219)."""

    block = INSTRUCTION_BLOCKS[block_name]
    assert forbidden in block, (block_name, rule, forbidden)
    assert licensed in block, (block_name, rule, licensed)


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


def test_every_canned_corpus_response_is_generated_voice_only() -> None:
    """§16.14 (#189): the demo corpus's §15 responses carry no owner name.

    Every string in these files is Exp2Res-generated voice — fact claims,
    gap questions, contradiction titles, self-claims, verifier reasons,
    counterevidence statements, suggested rewrites. §16.14 licenses exactly
    second person and subject-free there, so neither a third-person role
    noun nor the persona's own name may appear, and that is why the whole
    directory is marker-exempt rather than marker-carrying.
    """

    responses = sorted(CORPUS_PROSE_DIRECTORY.glob("*.json"))
    assert responses, "the canned §15 response corpus is missing"
    for path in responses:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for leaf in string_leaves(payload):
            lowered = leaf.lower()
            for pattern in FORBIDDEN_OWNER_NOUN_PATTERNS:
                assert not pattern.search(lowered), (path.name, leaf)
            assert "vera example" not in lowered, (path.name, leaf)


def test_the_marker_exemption_covers_exactly_the_generated_prose_corpus() -> None:
    """The two exemption lists cannot drift apart (#189).

    `examples/vera/corpus.py` derives its exempt set from the response
    tables it generates, while `scripts/check_public_hygiene.py` keeps a
    closed literal list by design. Adding a canned response without
    exempting it — or exempting a fixture that is not generated prose —
    fails here instead of at whichever checker runs first.
    """

    corpus = load_module("vera_corpus", "examples/vera/corpus.py")
    hygiene = load_module("public_hygiene", "scripts/check_public_hygiene.py")

    derived = {
        f"examples/vera/corpus/{relative}"
        for relative in corpus.generated_prose_paths()
    }
    listed = {
        path
        for path in hygiene.MARKER_EXEMPT_PATHS
        if path.startswith("examples/vera/corpus/")
    }
    assert derived == listed
    assert {path.name for path in CORPUS_PROSE_DIRECTORY.glob("*.json")} == {
        path.rsplit("/", 1)[1] for path in derived
    }
