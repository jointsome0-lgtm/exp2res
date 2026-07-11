## §14. CLI Specification

§14 is the sole canonical home of CLI command forms. Pipeline and consumer sections cite the owning §14 subsection instead of repeating shell syntax.

A noun-led generation group always uses an explicit `generate` subcommand; no bare noun invokes generation. Required source paths are positional; stored-record selectors and named generation/export context use options.

## §14.1 Initialize Project

```bash
exp2res init
```

Creates:

```text
.exp2res/
  exp2res.sqlite
  config.toml
out/
```

SQLite is the only managed store for raw logs and evidence items. Source files remain at their supplied paths as provenance references (§14.2, §14.5); `out/` is reserved for Stage 12 exports (§13.12).

## §14.2 Add Daily Log

```bash
exp2res log today
exp2res log today --project Exp2Res
exp2res log today --file notes/today.md
```

Every form persists `RawLog(entry_type=manual_daily, source_type=manual_entry)` and a linked `EvidenceItem(strength=manual_claim)`. `--file` reads the supplied file into `RawLog.raw_text` and records its path in `RawLog.external_ref`; the database remains the persisted record.

## §14.3 Add Retrospective Log

```bash
exp2res log retro
```

Interactive prompts:

```text
What period are we reconstructing?
How precise is this?
How confident are you?
Project/activity?
Describe what you remember.
```

The command persists `RawLog(entry_type=manual_retro, source_type=user_memory)` and a linked `EvidenceItem(strength=manual_claim)`.

## §14.4 Add Correction

```bash
exp2res correction add --log-id log_001
```

Stores `RawLog(entry_type=correction, source_type=manual_entry)` and a linked `EvidenceItem(strength=manual_claim)` as a new capture operation.

## §14.5 Import Evidence

```bash
exp2res import tick-like path/to/export.jsonl
exp2res import atlas path/to/atlas-export.json
exp2res import github --repo owner/name
exp2res import file docs/design.md --project Exp2Res
```

V1 mappings:

| Importer | Accepted payload | `RawLog.entry_type` | `RawLog.source_type` | `EvidenceItem.strength` |
|----------|------------------|---------------------|----------------------|-------------------------|
| `tick-like` | §19.1 event | `tick_like_event` | `imported_event` | `imported_activity_event` |
| `atlas` | §19.2 artifact reference | `atlas_artifact_ref` | `imported_artifact` | `artifact_reference` |
| `github` | §19.3 commit | `github_commit` | `imported_artifact` | `commit_or_pr` |
| `file` | local design document | `design_doc` | `imported_artifact` | `design_doc` |

`import file` rejects other local-file categories in V1 rather than guessing an entry type. It stores the document content in `RawLog.raw_text`, records the supplied path in `RawLog.external_ref` and `EvidenceItem.path`, and does not create a managed source copy.

## §14.6 Extract Facts

```bash
exp2res extract
exp2res extract --log-id log_001
exp2res facts list
exp2res facts show --fact-id fact_001
```

## §14.7 Generate Gaps and Contradictions

```bash
exp2res gaps generate
exp2res gaps answer --gap-id gap_001
exp2res contradictions generate
exp2res contradictions list
exp2res contradictions show --contradiction-id contradiction_001
```

`gaps answer` persists `RawLog(entry_type=gap_answer, source_type=manual_entry)` plus a linked `EvidenceItem(strength=manual_claim)`, then assigns the new raw-log ID to `GapQuestion.answer_log_id` and sets `GapQuestion.answered = true` in the same transaction; `answered` is true iff `answer_log_id` is set.

## §14.8 Generate Self-Signals

```bash
exp2res signals generate
exp2res signals list
```

## §14.9 Generate Self-Assessment

```bash
exp2res assess generate
exp2res assess generate --scope project --project Exp2Res
exp2res assess show --snapshot snapshot_001
exp2res assess verify --snapshot snapshot_001
exp2res export assessment --snapshot snapshot_001
```

## §14.10 Resume Export Flow

```bash
exp2res jd add jobs/agent_engineer.md
exp2res resume generate --jd jd_001 --branch agent-engineer
exp2res verify --branch agent-engineer
exp2res export resume --branch agent-engineer
```

## §14.11 Inspect Raw Logs

```bash
exp2res logs list
```

---

