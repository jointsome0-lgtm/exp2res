"""Offline §16.13 mixed-script tripwire tests over §15.1 response validation."""

from __future__ import annotations

import json
from pathlib import Path
import unicodedata

import pytest

from exp2res.errors import LLMInvocationError
from exp2res.llm.adapter import invoke_contract
from exp2res.llm.contracts import (
    ContractValidationError,
    mixed_script_tokens,
    mixed_script_tokens_in_json,
    validate_output,
)
from exp2res.llm.registry import LLMSelection
from exp2res.services.capture import capture_daily
from exp2res.storage.repository import get_experience_fact
from exp2res.storage.workspace import read_database

from conftest import FIXED_NOW
from fakes import FakeContractRunner
from test_facts_storage import make_fact, persist_fact
from test_llm_runner import (
    CONTRACT,
    SampleContractInput,
    SampleContractOutput,
    budgets,
    enrich,
    telemetry,
)


pytestmark = pytest.mark.contract


# А/н/а/л/и/т/и are Cyrillic; the trailing "ka" is Latin — one mixed token.
MUTANT_TOKEN = "Аналити" + "ka"
# Я is Cyrillic; "ndex" is Latin — a legitimately mixed owner-written token.
OWNER_TOKEN = "Я" + "ndex"


def test_separators_split_tokens_and_single_script_runs_never_register() -> None:
    """§16.13: hyphens, digits, and spaces terminate tokens before mixing."""

    assert mixed_script_tokens("Кафка-consumer") == frozenset()
    assert mixed_script_tokens("Кафка2consumer") == frozenset()
    assert mixed_script_tokens("Городская Аналитика shipped analytics") == frozenset()
    assert mixed_script_tokens(f"the {MUTANT_TOKEN} pipeline") == frozenset(
        {MUTANT_TOKEN}
    )


def test_combining_marks_join_tokens_without_carrying_a_script() -> None:
    """§16.13: U+0300–U+036F joins the maximal run but brings no script."""

    # Cyrillic Ра + combining acute (no NFC composition) + Latin y.
    stressed = "\u0420\u0430\u0301y"
    assert mixed_script_tokens(stressed) == frozenset({stressed})
    assert mixed_script_tokens("\u0301abc") == frozenset()
    leading = "\u0301\u042fa"  # a leading mark joins the run it precedes
    assert mixed_script_tokens(leading) == frozenset({leading})


def test_tokens_compare_under_nfc() -> None:
    """\u00a716.13: composed and decomposed spellings share one token identity."""

    composed = "\u041f\u0440\u00e9lude"  # Cyrillic \u041f\u0440 + precomposed Latin \u00e9
    decomposed = "\u041f\u0440e\u0301lude"  # the same run with \u00e9 decomposed
    assert mixed_script_tokens(decomposed) == frozenset({composed})
    assert mixed_script_tokens(composed) == mixed_script_tokens(decomposed)


def test_tokenization_precedes_nfc_so_composition_cannot_hide_a_token() -> None:
    """PR #207 review: NFC-first would split a run whose pair composes out
    of the closed classes; raw-code-point tokenization keeps it mixed."""

    # Я + e + U+0323: NFC composes e+U+0323 to U+1EB9, outside the Latin
    # class, so normalize-then-tokenize would see a lone Cyrillic run.
    hidden = "\u042fe\u0323"
    assert mixed_script_tokens(hidden) == frozenset(
        {unicodedata.normalize("NFC", hidden)}
    )


def test_json_walk_collects_tokens_from_every_nested_string() -> None:
    assert mixed_script_tokens_in_json(
        {
            "subject": f"shipping {OWNER_TOKEN} search",
            "nested": [{"note": MUTANT_TOKEN}, 42, None, True],
        }
    ) == frozenset({OWNER_TOKEN, MUTANT_TOKEN})


def test_validate_output_gates_on_the_input_carried_allowance() -> None:
    """§15.1/§16.13: input tokens pass; an invented one fails content-free."""

    allowed = frozenset({OWNER_TOKEN})
    accepted = json.dumps(
        {"value": f"Vera Example ships {OWNER_TOKEN}", "warnings": []},
        ensure_ascii=False,
    ).encode("utf-8")
    output = validate_output(
        CONTRACT, accepted, enrich=enrich, allowed_mixed_script_tokens=allowed
    )
    assert isinstance(output, SampleContractOutput)

    rejected = json.dumps(
        {
            "value": "Vera Example",
            "warnings": [{"type": "note", "message": MUTANT_TOKEN}],
        },
        ensure_ascii=False,
    ).encode("utf-8")
    with pytest.raises(ContractValidationError) as caught:
        validate_output(
            CONTRACT, rejected, enrich=enrich, allowed_mixed_script_tokens=allowed
        )
    diagnostics = caught.value.diagnostics
    assert MUTANT_TOKEN.encode("utf-8") not in diagnostics
    assert json.loads(diagnostics)["errors"] == [
        {"location": ["warnings", "0", "message"], "type": "mixed_script_token"}
    ]


def invoke_with_subject(
    workspace: Path, fake: FakeContractRunner, *, run_id: str, subject: str
):
    return invoke_contract(
        workspace=workspace,
        runner=fake,
        contract=CONTRACT,
        input_payload=SampleContractInput(subject=subject),
        selection=LLMSelection("codex-cli", "gpt-test-vera-example"),
        budgets=budgets(),
        run_id=run_id,
        stage="13.test",
        cli_version="0.144.4-test",
        enrich=enrich,
        clock=lambda: FIXED_NOW,
        sleeper=lambda _seconds: None,
        jitter=lambda lower, _upper: lower,
    )


def test_invented_token_takes_the_validation_retry_and_recovers(
    workspace: Path,
) -> None:
    """§15.1/§16.13: the tripwire is response validation, so one retry runs."""

    mutant = json.dumps(
        {"value": f"Vera Example ships {MUTANT_TOKEN}", "warnings": []},
        ensure_ascii=False,
    ).encode("utf-8")
    clean = b'{"value":"Vera Example ships the analytics pipeline","warnings":[]}'
    fake = FakeContractRunner([mutant, clean])
    invoke_with_subject(
        workspace,
        fake,
        run_id="run_vera_mixed_retry",
        subject="Vera Example builds Городская Аналитика",
    )
    _run, call = telemetry(workspace, "run_vera_mixed_retry")
    assert len(fake.calls) == 2
    assert call["schema_retries"] == 1
    assert call["status"] == "completed"
    retry_diagnostics = fake.calls[1].validation_errors
    assert b"mixed_script_token" in retry_diagnostics
    assert MUTANT_TOKEN.encode("utf-8") not in retry_diagnostics

    fake_twice = FakeContractRunner([mutant, mutant])
    with pytest.raises(LLMInvocationError) as caught:
        invoke_with_subject(
            workspace,
            fake_twice,
            run_id="run_vera_mixed_fail",
            subject="Vera Example synthetic input",
        )
    assert caught.value.failure_code == "response_validation_failed"


def test_input_carried_token_is_accepted_without_retry(workspace: Path) -> None:
    """§16.13: the adapter collects the call's input tokens as the allowance."""

    echoing = json.dumps(
        {"value": f"Vera Example ships {OWNER_TOKEN} search", "warnings": []},
        ensure_ascii=False,
    ).encode("utf-8")
    fake = FakeContractRunner([echoing])
    result = invoke_with_subject(
        workspace,
        fake,
        run_id="run_vera_mixed_carried",
        subject=f"Vera Example ships {OWNER_TOKEN} search",
    )
    _run, call = telemetry(workspace, "run_vera_mixed_carried")
    assert len(fake.calls) == 1
    assert call["schema_retries"] == 0
    assert call["status"] == "completed"
    assert OWNER_TOKEN in result.output.value


def test_stored_mixed_script_content_hydrates_without_tripwire(
    workspace: Path,
) -> None:
    """§16.13 binds model responses only; §12 hydration stays ungated."""

    bundle = capture_daily(
        workspace,
        raw_text=f"Vera Example builds {OWNER_TOKEN}",
        project=None,
        clock=lambda: FIXED_NOW,
    )
    fact = make_fact(
        raw_log_id=bundle.raw_log.id,
        evidence_item_id=bundle.evidence_items[0].id,
        claim=f"Maintained the {MUTANT_TOKEN} ingestion path.",
        technologies=[OWNER_TOKEN],
        project=None,
    )
    persist_fact(workspace, fact)
    with read_database(workspace) as connection:
        hydrated = get_experience_fact(connection, fact.id)
    assert hydrated is not None
    assert MUTANT_TOKEN in hydrated.claim
    assert hydrated.technologies == [OWNER_TOKEN]
