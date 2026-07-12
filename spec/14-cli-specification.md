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

`--log-id` must resolve to an existing raw record. The command requires self-contained correction text. Its temporal prompt starts from the target's `OccurredAt`; unless the owner explicitly replaces that placement, the correction copies it exactly, so every correction stores an effective temporal value without silently increasing precision.

In one database transaction, the command stores `RawLog(entry_type=correction, source_type=manual_entry, corrects_log_id=log_001)` plus its linked `EvidenceItem(strength=manual_claim)` while invalidating the exact current layers listed in §13.13. It attempts to remove managed exports before invoking the selected-lineage recompute in §14.12, which rebuilds through Stage 5; the command reports every invalidated assessment view and branch with its §14.9/§14.10 regeneration command (§13.13 rule 9). The target raw record is unchanged. If invalidation cleanup or recomputation fails, the correction remains stored, stale derivations stay unavailable, residual managed paths are reported, and the command exits unsuccessfully with `exp2res recompute --log-id log_001` as the retry.

## §14.5 Import Evidence

```bash
exp2res import tick-like path/to/export.jsonl
exp2res import atlas path/to/atlas-export.json
exp2res import github path/to/github-commit.json
exp2res import file docs/design.md --project Exp2Res
```

V1 mappings:

| Importer | Accepted payload | `RawLog.entry_type` | `RawLog.source_type` | `EvidenceItem.strength` |
|----------|------------------|---------------------|----------------------|-------------------------|
| `tick-like` | §19.1 event | `tick_like_event` | `imported_event` | `imported_activity_event` |
| `atlas` | §19.2 artifact reference | `atlas_artifact_ref` | `imported_artifact` | `artifact_reference` |
| `github` | §19.3 commit | `github_commit` | `imported_artifact` | `commit_or_pr` |
| `file` | local design document | `design_doc` | `imported_artifact` | `design_doc` |

Every importer consumes a user-supplied local payload or file. The `github` form reads one local §19.3 payload whose `repo` field identifies the repository; it does not fetch from GitHub or call any network. Remote acquisition is outside Exp2Res under §29.

`import file` rejects other local-file categories in V1 rather than guessing an entry type. It stores the document content in `RawLog.raw_text`, records the supplied path in `RawLog.external_ref` and `EvidenceItem.path`, and does not create a managed source copy.

## §14.6 Extract Facts

```bash
exp2res extract
exp2res extract --log-id log_001
exp2res facts list
exp2res facts show --fact-id fact_001
```

Extraction follows the correction-lineage replacement and current-generation rules in §13.3. Re-running it never appends a second current fact generation; if facts change, higher current generations are invalidated — §14.12 rebuilds Stages 4–5, while assessment views and resume branches require their owning §14.9/§14.10 generation commands (§13.13).

## §14.7 Generate Detections; Inspect and Answer Gaps and Contradictions

```bash
exp2res detections generate
exp2res gaps list
exp2res gaps answer --gap-id gap_001
exp2res contradictions list
exp2res contradictions show --contradiction-id contradiction_001
```

`detections generate` is the sole direct detection-generation command; Stage 4 also runs inside the §14.12 lifecycle flow (§13.4). Either path performs one complete §15.8 call whose result atomically retains or replaces the complete gap and contradiction generations together under §13.4's content-equivalence rule — never one half. Its help and command output must make the both-sets replacement side effect unmistakable and report both complete result sets plus every invalidated artifact class, or state that the generation was retained unchanged.

`gaps answer` persists `RawLog(entry_type=gap_answer, source_type=manual_entry)` plus a linked `EvidenceItem(strength=manual_claim)`, then assigns the new raw-log ID to `GapQuestion.answer_log_id` and sets `GapQuestion.answered = true` in the same transaction; `answered` is true iff `answer_log_id` is set. That transaction supersedes no current `AssessmentSnapshot`, branch, or bullet referencing the question: the answer is new raw evidence that reaches derived state only through extraction and regeneration (§13.5 via Stage 3, §13.6), while §17 renders the question's answered state on the still-current snapshot and §13.12 keeps that snapshot exportable. It does apply §13's managed-output invalidation semantics to the exports the answer makes stale: while any current snapshot references the answered question, it enumerates the managed `out/assessment/<view>/` directory of every current snapshot referencing that question — with complete unfiltered gap sets, that is every current view — and the managed `out/<branch>/` set of every current branch anchored to such a snapshot, attempts removal, and reports every residual path as an unsuccessful invalidation. Database state remains committed regardless; the snapshot and branches stay immediately re-exportable with the answered-since-synthesis rendering.

Gap answers are self-contained at capture, like corrections: the command copies the answered question's text and `GapQuestion.reason` into the answer's `RawLog.metadata` (`question_text`, `question_reason`). The answer therefore remains interpretable evidence if its question row is later superseded by a Stage 4 regeneration or purged by the §13.13 reset. Question-to-answer links are never re-created after regeneration: an uncertainty a stored answer resolves simply no longer fires its gap trigger against the current facts, and a gap that regenerates anyway is genuinely still open. The copied question text becomes part of the owner's raw record — owner-deletable on its own, never system-edited.

V1 gap and contradiction subcommands only inspect immutable Stage 4 detections or answer gaps; no `gaps` or `contradictions` form generates, and detection generation happens only through `detections generate` or the §14.12 lifecycle flow. There is no resolve, dismiss, or resolution-note command. Outside the §13.13 owner-deletion privacy reset, a conflict disappears from the current set only when the current Stage 4 inputs no longer conflict and a successful replacement generation omits it.

## §14.8 Generate Self-Signals

```bash
exp2res signals generate
exp2res signals list
```

## §14.9 Generate Self-Assessment

```bash
exp2res assess generate
exp2res assess generate --scope project --project Exp2Res
exp2res assess list
exp2res assess show --snapshot snapshot_001
exp2res assess verify --snapshot snapshot_001
exp2res export assessment --snapshot snapshot_001
```

`--scope` selects one canonical §10 `AssessmentScope` value and defaults to `global` when omitted. `--scope project` requires a `--project` value that is non-blank after canonicalization — Unicode NFC normalization plus leading/trailing whitespace trim. Stage 6 stores that canonical value as `AssessmentSnapshot.scope_target`; the LLM receives it as structural context but cannot author or normalize it further. Replacement identity and subject matching use its locale-independent case-folded form (§11.7, §13.6). `global` takes no target and persists `scope_target = None`. No scope value list is duplicated here; `AssessmentScope` in §10 is canonical, and a retired scope value returns only with its deterministic selection semantics.

`assess list` reports every current snapshot — ID, scope, scope target, verification status, creation time — as the discovery surface for explicit `--snapshot` selectors across simultaneously current views; it generates nothing.

`assess verify` is required before assessment export. `export assessment` rejects `unverified` and every other snapshot status outside the assessment-export allowlist in §16.11.

V1 defines no claim-confirm, dispute, or override command. `assess verify` is the system verifier gate defined by §5.10, not an owner verdict stored on a regenerated claim.

`assess verify` presents every complete §15.5 finding, including `reason` and `suggested_rewrite`, to the owner. The suggestion is advisory: it is neither persisted nor applied, and verification never invokes `assess generate`. The owner may add or correct raw evidence and request a new assessment generation; any changed claim wording belongs to that new Stage 6 generation.

## §14.10 Resume Export Flow

```bash
exp2res jd add jobs/agent_engineer.md
exp2res resume generate --jd jd_001 --snapshot snapshot_001 --branch agent-engineer
exp2res verify --branch agent-engineer
exp2res export resume --branch agent-engineer
```

`--snapshot` is a required stored-record selector for the exact assessment anchor governed by §18. It has no latest-snapshot default. A missing, superseded, `unverified`, or otherwise Stage-10-ineligible snapshot fails before a branch or bullet is inserted; the persisted branch records exactly the selected ID.

`--jd` must resolve to a persisted typed `JobDescription`; Stage 10 copies that exact ID into the candidate `ResumeBranch.job_description_id` so verification and export can resolve every matched requirement. A Stage 10 candidate that omits or changes the selected ID fails atomically.

`--branch` is a single path segment: it may not contain `/` or `\`, may not be `.` or `..`, and may not equal `assessment` compared case-folded — `out/assessment/` is the reserved per-view assessment namespace (§13.12), and no branch directory may fall under or collide with it.

`verify --branch` performs the one Stage 11 semantic pass and presents its complete findings, including advisory `suggested_rewrite` values; it never applies a suggestion or invokes `resume generate`. Changed bullet wording requires a later explicit `resume generate` command and a replacement branch generation.

## §14.11 Manage Raw Logs

```bash
exp2res logs list
exp2res logs delete --log-id log_001
exp2res logs delete --log-id log_001 --yes
```

`logs delete` is the owner's destructive privacy operation. It reports the selected record and known external source path, requires interactive confirmation unless `--yes` is supplied, and performs the global purge/delete/rebuild flow in §13.13, whose automatic rebuild ends at Stage 5; the purged assessment views and branches are reported with their §14.9/§14.10 regeneration commands as command output only (§13.13 rule 9). It deletes only Exp2Res-managed database records and `out/`; it does not delete a supplied source file or export copied elsewhere. Raw database deletion remains committed if output removal or rebuilding fails; residual managed paths are reported as `deletion_incomplete`, never as success.

## §14.12 Recompute Derived State

```bash
exp2res recompute
exp2res recompute --log-id log_001
```

The no-selector form rebuilds from every retained correction lineage. `--log-id` is a named stored-record selector and rebuilds that record's lineage before the global Stage 4–5 regeneration. This command orchestrates the existing Stage 3–5 triggers under §13.13; it is not a new pipeline stage and does not create a synthetic stage identifier.

Lifecycle recomputation ends at Stage 5 and performs no Stage 6 or Stage 7 call, so it presents no verifier findings. `recompute`, and the correction and deletion commands that invoke the same flow, report every invalidated assessment view and branch with its explicit §14.9/§14.10 regeneration command; a retry that finds no current view reports that state instead of inferring a desired view set (§13.13 rule 9).

---
