# Vera Example fixture corpus

`vera-example-fixtures` **v0.1.0** — the one reusable public evidence
corpus for Exp2Res tests, documentation, screenshots, and demos
([#78](https://github.com/jointsome0-lgtm/exp2res/issues/78)).

Everything here is invented demo data authored by the ecosystem's one
synthetic persona, **Vera Example** (canonical fact sheet:
[selfos `docs/persona.md`](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/persona.md)).
Every person, company, repository, metric, date, path, and vacancy is
fictional; nothing is sanitized from real records. The literal,
case-sensitive string `Vera Example` appears in every fixture file — it
is the ecosystem-wide CI-hygiene grep marker, and `corpus.py` enforces
it at generation time.

## One command

```bash
python3 examples/vera/corpus.py generate   # regenerate corpus/ from the canonical source
python3 examples/vera/corpus.py check      # verify corpus/ is byte-identical to the source
```

The canonical source is the `STORY` section of `corpus.py` (stdlib-only,
offline, no wall clock, no randomness): repeated generation is
byte-stable, and `check` fails on a missing, stale, or unexpected file.
`manifest.json` records the corpus name, version, persona pointer, and a
SHA-256 per generated file.

## Layout

```text
corpus/
  manifest.json     corpus identity + per-file SHA-256
  replay.json       ordered end-to-end replay contract (below)
  logs/             §14.2 daily-log files, §14.3 retro scripts, §14.4 correction scripts
  imports/          §19.4 envelopes (ephemeris JSONL, atlas JSON, github JSON),
                    §14.5 `import file` design doc, referenced artifacts/
  jds/              §14.10 `jd add` vacancy texts
  invalid/          deterministic failure fixtures, each wrong in exactly one way
```

Integration envelopes carry real `content_hash` values: SHA-256 over the
§11 canonical-serialization bytes of `body` (keys sorted by code point,
no insignificant whitespace, datetimes normalized to UTC
`…Z`-with-microseconds for hashing only; stored values keep their
supplied `+02:00` offsets per §12 rule 3). If the implementation's §11
serializer ever disagrees with `corpus.py`, imports of this corpus fail
loudly — that disagreement is a spec-precision finding, not a fixture to
patch around.

## Replay contract (`replay.json`)

`replay.json` is the ordered end-to-end path from an empty workspace to
a verified mirror and a bullet-pack attempt. Steps use semantic kinds —
`log_daily`, `log_retro`, `correction_add`, `import`, `jd_add`,
`logs_delete`, `jd_delete` — that a harness maps to the §14 commands
(`exp2res log today --file … --project …`, `exp2res log retro`,
`exp2res correction add`, `exp2res import <importer> <file>`,
`exp2res jd add`, `exp2res logs delete`, `exp2res jd delete`). Retro and
correction steps replay the interactive §14.3/§14.4 prompts from their
JSON scripts; `target_story_key` resolves through the log created by the
named earlier step. Each step pins the workspace clock to its `clock`
value; `expect` carries the coarse §14.14 outcome (import class counts,
success, displacement). `failure_steps` run after their `after_step` and
must fail exactly as stated; the `privacy_epilogue` exercises the
§13.13 deletion lifecycles after the main path completes.

## Coverage matrix (issue #78)

| Coverage item | Fixture(s) | Expected behavior |
| --- | --- | --- |
| Daily and retrospective raw logs | `logs/daily-*.md`, `logs/retro-*.json` | `manual_daily`/`manual_retro` raw logs with `manual_claim` evidence (§14.2–§14.3) |
| Approximate and exact occurrence times | retros (`approximate_range`, `low`/`medium`), ephemeris + github (`exact_datetime`), atlas (`date_range`) | precision-preserving `OccurredAt` (§11.1), no precision inflation (§5) |
| Corrections and superseded interpretations | `logs/correction-2026-07-03-k8s.json` → `retro-2026-06-k8s` | whole-record displacement, copied placement/project (§13.3 r10, §14.4) |
| Manual claims, design docs, activity imports, artifact refs, commits | logs, `imports/design-doc-k8s-playbook.md`, `imports/ephemeris-2026-06.jsonl`, `imports/github-commit-*.json` | one `EvidenceItem` strength per §14.5 mapping |
| Canonically equivalent and distinct project labels | replay step 2 `k8s playbook` vs `K8s Playbook`; `Strength Basics` | one folded project view for the first pair, a distinct second view (§11, §13.6) |
| Weak, conflicting, artifact-backed evidence | strength retro (weak), `daily-2026-06-25` vs `daily-2026-07-02`+ephemeris e3 (conflicting), commit gh1 + atlas artifact (artifact-backed) | §9.4 calibration material |
| Gaps, contradictions, counterevidence preconditions | overclaiming retros, the finished/unfinished ingress pair, `daily-2026-06-20` vs the consistency claim | Stage 4 detections; knee log is counterevidence to "trained consistently" |
| Global and ≥2 project assessment views | the two projects plus unscoped material (`daily-2026-07-08`) | `global`, `K8s Playbook`, `Strength Basics` views (§13.6) |
| JDs with required/preferred requirements | `jds/jd-docs-engineer-examplia.md`, `jds/jd-junior-backend-clouddocs.md` | typed `JDRequirementKind` parsing (§13.8) |
| Supported and rejected resume bullets | docs-writer JD vs the evidence; backend JD's production/on-call requirements | supported bullets for documented k8s-docs work; blocked claims for production Python/on-call (§16, §18) |
| Prompt-injection-like source text stays inert | `logs/daily-2026-07-08.md` (quoted forum comment), backend JD's "Note to agents" | byte-exact preservation, no authority (§16.12, §21.49) |
| Private/sensitive routing markers, invented only | `logs/daily-2026-06-20.md` (knee pain) | stand-in for sensitive content; `privacy_epilogue` P1 deletes it (§13.13); the human-only `private` marker itself stays reserved-unimplemented (§11) |
| Duplicate/conflict/invalid import behavior | duplicate line in the ephemeris JSONL; `invalid/*` | counted duplicate; fail-closed conflict and rejections (§19.4) |

## Consumer contract

Consumers (#71 harness, #29 README walkthrough, #75 demo views, #65
injection/privacy regressions, #79 recorded demo, lifecycle/migration
fixtures) select subsets by directory or story key but never fork the
persona's facts or identities: a new biographical fact lands in selfos
`docs/persona.md` first, then here. Versioning is semantic: additive
files bump the minor version; changing any existing generated byte bumps
the major version and names its consumers in the PR.

Deliberately not here yet, and planned as additive layers once their
owning consumers exist: canned §15 fake-runner responses and golden
expected outputs (#71 defines the fake-runner layer; goldens only where
§21 fixes service-owned determinism — with no implementation, nothing
qualifies today), and the §14.7 gap-answer scripts.
