# Exp2Res — Experience to Self-Assessment to Verified Bullet Pack

> A mirror first. A verified-bullet-pack exporter second.

Exp2Res is a local-first, provenance-heavy self-assessment system.

It turns owner-controlled experience evidence that automation cannot rewrite into an honest model of skills, patterns, gaps, contradictions, and uncertainty. A verified bullet pack is a secondary export: job-targeted bullets are generated only from supported evidence and verified before export. A full resume document model is deferred to a post-mirror iteration.

## Project stage: implementation-ready with controlled amendments

SDD v0.3 is implementation-ready and remains the binding product contract. The manual-capture and private-workspace foundation is implemented, together with isolated Codex and Claude agent-backed runner substrates and Stage 3 fact extraction through the §14.6 CLI; current work proceeds phase by phase along the approved dependency frontier summarized in [`AGENTS.md`](AGENTS.md). This is not yet a complete Mirror, verified bullet-pack, or resume product. After [#97](https://github.com/jointsome0-lgtm/exp2res/issues/97) implements and verifies the global Mirror, the first browser target is [#98](https://github.com/jointsome0-lgtm/exp2res/issues/98): that Mirror plus unanswered Gap Questions on loopback. Project-scoped mirrors and the JD-to-bullet-pack browser workflow are later slices.

§22 phase status: Phase 0 complete; Phase 1 complete through the fact-extraction CLI; Phases 2–5 not yet implemented.

Exp2Res remains a public engine. Real owner data lives in a private workspace outside this repository, never in the public checkout.

## What is in this repository

- [`SDD.md`](SDD.md) — the map: a stable-numbered § index. Each section lives in its own file under [`spec/`](spec/) (`spec/NN-slug.md`).
- [`spec/`](spec/) — the specification body: domain model, typed contracts, SQLite schema, pipeline stages, CLI, LLM contracts, verification rules, evals, and acceptance criteria.
- [`DECISION-LOG.md`](DECISION-LOG.md) — dated one-line design decisions with rejected alternatives.
- [`exp2res/`](exp2res/) — the implemented Python package, currently covering workspace management, manual capture, owner deletion, the isolated Codex and Claude runner substrates, Stage 3 fact extraction, and raw-log/fact inspection.
- [`tests/`](tests/) — offline tests for the implemented behavior.
- [`scripts/`](scripts/) — repository-owned offline checks: public hygiene, the SDD-conventions and Decision Log linters vendored from selfos-skills with recorded versions, and the aggregate `scripts/check.py`; all validate in a fresh offline checkout.

To read the design, start with the § index in `SDD.md` and open the section files you need; §1 (executive summary) and §28 (final design statement) are the shortest complete picture.

## Design boundaries

- **Public engine, private workspace.** This repository is a public engine in the [selfos topology](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/architecture.md): specification, docs, code, and invented fixtures only. The owner's canonical store is a [private SQLite workspace](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md) that lives outside this public repository — a public checkout is never a data destination. `EXP2RES_WORKSPACE` is reserved for locating it; the path discovery order is an explicit flag, then `EXP2RES_WORKSPACE`, then `instances.exp2res` in `~/.config/selfos/config.toml`. Real data and managed outputs never enter this repository. The deletion guarantee (logical deletion vs. purge, and what honestly remains) is canonical in the [selfos deletion contract](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/deletion.md) and is not restated here; exp2res's own managed-data lifecycle inventory is defined in [spec §29](spec/29-security-and-privacy.md).
- **Local-first and private by default.** The owner's workspace is the only canonical store; nothing is sent anywhere on system initiative. LLM calls happen only inside explicit, user-initiated pipeline runs against an explicitly selected provider.
- **Evidence over impressiveness.** Raw records are append-only to automation, every derived claim traces to evidence, contradictions are first-class, and verifier gates block flattery, inflated ownership, invented metrics, and unsupported generated-bullet claims.
- **The export never becomes the master model.** Experience model → self-assessment → export projection → verified bullet pack, in that order.

## Ecosystem

Exp2Res is the experience layer of [selfos](https://github.com/jointsome0-lgtm/selfos), a personal state platform, alongside [ephemeris](https://github.com/jointsome0-lgtm/ephemeris) (activity) and [atlas](https://github.com/jointsome0-lgtm/atlas) (knowledge).

## Security

The repository contains runnable implementation slices, including local workspace/capture behavior and isolated runner infrastructure. The [ecosystem-wide security policy](https://github.com/jointsome0-lgtm/selfos/blob/main/SECURITY.md) governs vulnerability reporting, while [spec §29](spec/29-security-and-privacy.md) remains the canonical Exp2Res security, privacy, and managed-data boundary.

## LLM adapter selection

A private workspace selects its adapter, model, and reasoning effort explicitly in `.exp2res/config.toml`. Fresh workspaces use the conservative shared effort default and the Codex adapter:

```toml
[llm]
adapter = "codex-cli"
model = "gpt-5.6-sol"
reasoning_effort = "high"
codex_home_env = "CODEX_HOME"
claude_config_dir_env = "CLAUDE_CONFIG_DIR"
```

The registered agent-backed choices are `codex-cli` and `claude-agent-sdk`; the latter uses model `claude-opus-4-8`. Both use externally managed CLI sessions and fail closed when that session is absent. Sign in with the corresponding CLI outside Exp2Res before invoking a stage. Claude reads its session from the directory named by `claude_config_dir_env`, or `~/.claude` when that environment variable is unset. `reasoning_effort` must be supported by the selected adapter; `high` is supported by both and is the service default. The specification's `openai-compat` choice is not registered in this build yet.

## Demo

The issue [#79](https://github.com/jointsome0-lgtm/exp2res/issues/79) first-mirror demo is a deliberately small subset of the eventual full [§23 walkthrough](spec/23-end-to-end-demo.md). It exercises capture → extraction → detection → signals → global assessment → verification → assessment export, then a separate project-scoped overclaim whose rejected verification blocks export. It does not claim to implement §23's later verified bullet-pack surface.

All records and all canned model responses are invented for **Vera Example**. The runner is explicitly no-cost and deterministic: it invokes the public Typer app in-process, injects manifest-pinned responses through the production contract-runner seam, fixes clocks and IDs, and performs no network or provider call. The actual SQLite workspace is a checkout-specific temporary directory outside the public repository; output presents it only as the stable relative alias `demo/workspace`.

Run the complete offline flow from the repository root:

```bash
make demo-reset
make demo-run
make demo-verify
```

`demo-verify` proves manifest hashes, evidence-map closure through real fact/evidence/log rows, current-generation integrity, the blocked export, the checked transcript, and byte-identical exported members and transcript across two clean reruns. The accessible textual walkthrough is [`demo/transcript.txt`](demo/transcript.txt).

Version pin: Vera corpus `0.3.0`; SQLite schema `7`; CLI `envelope_version = 1`. After any incompatible corpus, schema, envelope, rendering, or command-flow change, regenerate with `python examples/vera/corpus.py generate`, rerun the three make targets, and replace the checked transcript only with the new exact deterministic output.

The owner recording is intentionally pending. Record it after merge with:

```bash
asciinema rec -c "make demo-reset demo-run" demo.cast
```

## Public hygiene

Before publishing or opening a pull request, run:

```bash
python3 scripts/check_public_hygiene.py
```

Every public repository must run the same local checker through both a pre-commit hook and a CI job. Both layers are required, not alternatives. Enable the committed pre-commit hook once per clone:

```bash
git config core.hooksPath .githooks
```

## License

[MIT](LICENSE)
