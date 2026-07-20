## §14. CLI Specification

§14 is the sole canonical home of CLI command forms. Pipeline and consumer sections cite the owning §14 subsection instead of repeating shell syntax.

A noun-led generation group always uses an explicit `generate` subcommand; no bare noun invokes generation. Required source paths are positional; stored-record selectors and named generation/export context use options.

The exhaustive workspace business-writer/read-only command classification and its locking behavior are specified in §8.1 and apply to every non-bootstrap form below.

§14.14 defines the global runtime contract — workspace discovery, configuration precedence, non-interactive behavior, exit codes, and the machine-readable result envelope — that binds every form in this section.

## §14.1 Initialize Project and Manage Database

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

SQLite is the only managed store for raw logs and evidence items. Source files remain at their supplied paths as provenance references (§14.2, §14.5); `out/` is reserved for Stage 12 exports (§13.12) published and managed only through §13.14.

If the workspace already exists at that current version, `init` succeeds as an idempotent no-op, reports the version, and changes no database row or managed path. At any other recognized version it fails closed, reports the mismatch, and points at `db status` plus the `db migrate` or application-upgrade recovery required by §12.14. An unrecognized existing database fails closed. A partially initialized target — including an existing `.exp2res/` without `exp2res.sqlite` or a database without readable `schema_meta` — is unrecognized: `init` fails closed and never completes, overwrites, or repairs it implicitly. A pre-existing non-empty `out/` is inert content: initialization neither reads, changes, nor removes anything beneath it, but before accepting the directory as the managed export namespace it restores the §29.2 owner-only mode on the `out/` directory entry itself, so managed path structure is not listable by other local users; if that permission change fails, `init` fails closed. `init` never re-creates, overwrites, or deletes existing workspace data.

```bash
exp2res db status
exp2res db migrate
```

`db status` reports compatibility and registered-path availability without writing; `db migrate` is the sole migration trigger (§12.14).

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

`--log-id` must resolve to an existing raw record. The command requires self-contained correction text. Under §13.3 rule 10, a correction displaces the target record's interpretation as a whole. Its text must therefore restate whatever from the target remains true, because displaced content it omits is not re-extracted; the copied `OccurredAt` and `project` mechanics below preserve placement and project provenance unless the owner explicitly changes them. Its temporal prompt starts from the target's `OccurredAt`; unless the owner explicitly replaces that placement, the correction copies it exactly, so every correction stores an effective temporal value without silently increasing precision. `project` follows the same capture rule: the prompt starts from the target's `project`, and unless the owner explicitly replaces or clears it, the correction copies it exactly — a text-only correction can never silently strip or change the label the §13.6 project views select by (§13.3 rule 13).

In one database transaction, the command stores `RawLog(entry_type=correction, source_type=manual_entry, corrects_log_id=log_001)` plus its linked `EvidenceItem(strength=manual_claim)` while invalidating the exact current layers listed in §13.13. Managed-export removal, the selected-lineage recompute, and invalidation reporting follow §13.13 rules 4 and 9 via §14.12. The target raw record remains unchanged as stored. If invalidation cleanup or recomputation fails, §13.13 rule 4's failure semantics apply, with the §14.12 `--log-id` form as the documented retry.

## §14.5 Import Evidence

```bash
exp2res import ephemeris path/to/export.jsonl
exp2res import atlas path/to/atlas-export.json
exp2res import github path/to/github-commit.json
exp2res import file docs/design.md --project Exp2Res
```

V1 mappings:

| Importer | Accepted payload | `RawLog.entry_type` | `RawLog.source_type` | `EvidenceItem.strength` |
|----------|------------------|---------------------|----------------------|-------------------------|
| `ephemeris` | §19.1 activity-domain record | `ephemeris_event` | `imported_event` | `imported_activity_event` |
| `atlas` | §19.2 knowledge-state snapshot | `atlas_snapshot` | `imported_artifact` | `knowledge_state_snapshot` |
| `github` | §19.3 commit | `github_commit` | `imported_artifact` | attribution-dependent under §19.3 (`OwnerAttribution` §10) |
| `file` | local design document | `design_doc` | `imported_artifact` | `design_doc` |

Every importer consumes a user-supplied local payload or file. The `github` form reads one local §19.3 payload whose `repo` field identifies the repository; it does not fetch from GitHub or call any network. Remote acquisition is outside Exp2Res under §29.

§19.4 owns the envelope, identity/idempotency, duplicate/conflict, and all-or-nothing batch semantics for the §19-backed `ephemeris`, `atlas`, and `github` forms (`import file` is not an envelope record); §14.14 rule 5 owns their closed result shape.

`import file` rejects other local-file categories in V1 rather than guessing an entry type. It stores the document content in `RawLog.raw_text`, records the supplied path in `RawLog.external_ref` and `EvidenceItem.path`, and does not create a managed source copy.

## §14.6 Extract Facts

```bash
exp2res extract
exp2res extract --log-id log_001
exp2res facts list
exp2res facts show --fact-id fact_001
```

Extraction follows the correction-lineage replacement and current-generation rules in §13.3; invalidation and regeneration reporting follow §13.3 rule 12 and §13.13 rule 9.

## §14.7 Generate Detections; Inspect and Answer Gaps and Contradictions

```bash
exp2res detections generate
exp2res gaps list
exp2res gaps answer --gap-id gap_001
exp2res contradictions list
exp2res contradictions show --contradiction-id contradiction_001
```

`detections generate` is the sole direct detection-generation command; Stage 4 also runs inside the §14.12 lifecycle flow (§13.4). Either path follows §13.4's whole-generation retain-or-replace lifecycle. Its help and command output must make the both-sets replacement side effect unmistakable and report both complete result sets, every invalidated artifact class, and the §13.13 rule 9 view/branch regeneration guidance, or state that the generation was retained unchanged.

`gaps answer` persists `RawLog(entry_type=gap_answer, source_type=manual_entry)` plus a linked `EvidenceItem(strength=manual_claim)`, then assigns the new raw-log ID to `GapQuestion.answer_log_id` and sets `GapQuestion.answered = true` in the same transaction; `answered` is true iff `answer_log_id` is set. That transaction supersedes no current `AssessmentSnapshot`, branch, or bullet referencing the question: the answer is new raw evidence that reaches derived state only through extraction and regeneration (§13.5 via Stage 3, §13.6), while §17 renders the question's answered state on the still-current snapshot and §13.12 keeps that snapshot exportable. It is the gap-answer trigger of §13's stale-export invalidation rule: while any current snapshot references the answered question, the affected sets are that snapshot's `out/assessment/<snapshot-id>/` set and every `out/branch/<branch-id>/` set whose branch anchors it — with complete unfiltered gap sets, that is every current assessment view. The snapshot and branches stay immediately re-exportable with the answered-since-synthesis rendering.

Gap answers are self-contained at capture, like corrections: the command copies the answered question's text and `GapQuestion.reason` into the answer's `RawLog.metadata` (`question_text`, `question_reason`). The answer therefore remains interpretable evidence if its question row is later superseded by a Stage 4 regeneration or purged by the §13.13 reset. Question-to-answer links are never re-created after regeneration: an uncertainty a stored answer resolves simply no longer fires its gap trigger against the current facts, and a gap that regenerates anyway is genuinely still open. The copied question text becomes part of the owner's raw record — owner-deletable on its own, never system-edited.

V1 gap and contradiction subcommands only inspect immutable Stage 4 detections or answer gaps; no `gaps` or `contradictions` form generates, and detection generation happens only through `detections generate` or the §14.12 lifecycle flow. There is no resolve, dismiss, or resolution-note command. Outside the §13.13 raw-log owner-deletion privacy reset, a conflict disappears from the current set only when the current Stage 4 inputs no longer conflict and a successful replacement generation omits it.

## §14.8 Generate Self-Signals

```bash
exp2res signals generate
exp2res signals list
```

A changed signal generation supersedes every current claim, snapshot, branch, and bullet (§13.5); `signals generate` reports the invalidated classes and the §13.13 rule 9 view/branch regeneration guidance.

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

`assess verify` is required before assessment export: `export assessment` applies §16.11's assessment-export allowlist.

On success, `export assessment` publishes the selected snapshot only at its §13.14 ID-keyed path and returns only the manifest-validated §14.14 export result. Scope and scope-target values remain identity and display data; neither becomes a path component.

V1 defines no claim-confirm, dispute, or override command. `assess verify` is the system verifier gate defined by §5.10, not an owner verdict stored on a regenerated claim.

`assess verify` presents every complete §15.5 finding, including `reason` and `suggested_rewrite`, to the owner. The suggestion follows the §13.7 advisory-rewrite lifecycle; revised claim wording belongs to a later explicit `assess generate` generation.

## §14.10 Verified Bullet-Pack Flow

```bash
exp2res jd add jobs/agent_engineer.md
exp2res bullets generate --jd jd_001 --snapshot snapshot_001 --branch agent-engineer
exp2res bullets verify --branch agent-engineer
exp2res bullets export --branch agent-engineer
```

`jd add` remains the Stage 8 creation command owned by this bullet-pack flow; §14.15 owns job-description inspection and deletion. The internal `ResumeBranch` and `ResumeBullet` entity names retain “resume” because a branch models targeting for a vacancy; renaming those persisted/internal contracts would be a §12.14 schema change, while the product-facing artifact and command group are the verified bullet pack and `bullets`.

`--snapshot` is a required stored-record selector governed by §18's canonical snapshot-anchor rule.

`--jd` must resolve to a persisted typed `JobDescription`; §13.10's exact-copy association rule governs the candidate branch.

`--branch` is a non-blank display name that must pass §11 structural-string hygiene; its exact owner spelling is stored in `ResumeBranch.name`. It has no path semantics: `/`, `\`, leading or trailing whitespace or `.`, dot-segment spellings, and the name `assessment` receive no path-specific rejection or reservation because §13.14 publishes only under `out/branch/<branch-id>/` and carries the name in `manifest.json`. Branch replacement identity remains the name's locale-independent case fold after Unicode NFC normalization: `--branch` selectors resolve a current branch by that folded identity, generating a name that folds equal to a current branch's supersedes exactly that branch (§13.10), and two current branches can never fold equal. The identity rule controls replacement and selection only; it never derives or aliases a managed path.

On success, `bullets export` publishes the selected current branch only at its §13.14 ID-keyed path and returns only the manifest-validated §14.14 export result.

`bullets verify` performs the one Stage 11 semantic pass and presents its complete findings, including advisory `suggested_rewrite` values. A suggestion follows the §13.11 advisory-rewrite lifecycle; revised bullet wording requires a later explicit `bullets generate` replacement generation. Under `--json`, all three forms report their canonical `bullets generate`, `bullets verify`, or `bullets export` command path through §14.14; generation and verification use the standard envelope fields with `result = null`, while export uses the closed manifest-path result below.

## §14.11 Manage Raw Logs

```bash
exp2res logs list
exp2res logs delete --log-id log_001
exp2res logs delete --log-id log_001 --yes
```

`logs delete` is the owner's per-raw-record destructive privacy operation. It reports the selected record and known external source path, requires interactive confirmation unless `--yes` is supplied, and performs the global purge/delete/rebuild flow in §13.13, whose automatic rebuild ends at Stage 5; the purged assessment views and branches are reported with the regeneration guidance of §13.13 rule 9 as command output only. Its deletion boundary, commit-despite-cleanup-failure, and residual-path semantics follow §13.13 rules 6 and 8. Job-description deletion and whole-workspace purge are the distinct §14.15 and §14.16 privacy operations.

## §14.12 Recompute Derived State

```bash
exp2res recompute
exp2res recompute --log-id log_001
```

The no-selector form rebuilds from every retained correction lineage. `--log-id` is a named stored-record selector and rebuilds that record's lineage before the global Stage 4–5 regeneration. Recompute orchestration and its `13.13` telemetry row are defined in §13.13; it remains not a pipeline stage.

Recompute completion at the Stage 5 endpoint and every invalidation report follow §13.13 rules 1 and 9 for this command and for the correction and deletion commands that invoke the same flow.

## §14.13 Inspect Processing Runs and Verification History

```bash
exp2res runs list
exp2res runs show --run-id run_001
```

`runs list` reports processing-run rows with stage, status, timing, and parent linkage. `runs show` reports the selected run's §12.13 run row, its §12.15 per-call telemetry rows, and its §11.14 verification findings when present. Both commands are read-only telemetry inspection, never a pipeline stage or stage trigger. §14.14 owns their exit-code and JSON-output details.

## §14.14 Global CLI Runtime Contract

This contract binds every command-specific form above and every later §14 addition.

1. **Workspace discovery.** Every non-`init` command resolves its workspace before loading workspace configuration or performing compatibility or business I/O. Starting at the current directory's canonical real path, with symlinks resolved, it examines that directory and each physical parent through the filesystem root; the first ancestor containing a `.exp2res/` directory is the workspace root. The nearest marker wins, including when that marker is partial or its database is unrecognized: compatibility then fails under §12.14 rather than skipping the marker and binding an enclosing workspace. The global `--workspace <path>` option replaces this walk; a relative value resolves from the current directory, its canonical real path alone is examined, and it must name a directory containing `.exp2res/`. An invalid override never falls back to discovery. Failure to establish a workspace uses a stable diagnostic class in exit class 3. `init` neither walks ancestry nor redirects through `--workspace`: it always targets the canonical current directory, and supplying the override is invalid usage. Creating `.exp2res/` there while an enclosing workspace exists is legal; later discovery from that tree selects the new nested workspace by the nearest-wins rule.
2. **Configuration precedence.** Workspace resolution precedes configuration loading. For each setting that declares more than one representation, the value is selected in this order: an explicitly supplied CLI flag, its documented `EXP2RES_*` environment variable, the corresponding key in the selected workspace's `.exp2res/config.toml`, then a built-in default when that setting declares one. A representation, including a default, need not exist at every level; a required setting still unresolved after this chain fails closed. An undocumented environment variable or ambient user, repository, provider, shell, or platform setting has no effect. `--workspace`, `--json`, `--yes`, and `--no-input` are invocation controls rather than configuration values and are accepted only as explicit flags. Provider credentials are outside this precedence chain and remain transport-only adapter values resolved at the §29.2/§29.4 boundary.
3. **Non-interactive behavior.** Every command accepts the global `--json`, `--yes`, `--no-input`, `--workspace`, `--verbose`, and `--quiet` controls subject to the `init` exception above. `--no-input` forces non-interactive behavior regardless of the terminal; non-TTY stdin is non-interactive even without that flag. In either case, a command that would prompt must receive every required value through its declared flags or fail closed with exit class 2 before blocking or performing the prompt-dependent operation. This applies to §14.3 capture, destructive confirmations, migration, and every foreground action that may invoke a cost-bearing LLM call. A destructive or cost-bearing action additionally requires explicit `--yes` in non-interactive mode; on TTY stdin it requires either `--yes` or an interactive confirmation before the destructive step or provider call. The confirmation set is `logs delete`, `jd delete` (§14.15), `workspace purge` (§14.16), `db migrate`, and every command that can make a cost-bearing §15 call, including verification, parsing, extraction, detection, lifecycle, and generation commands. `--yes` supplies consent only; it never supplies missing capture or selector input. Verbosity controls affect secret-safe diagnostics and progress only, never the result, exit class, or JSON shape.
4. **Exit-code taxonomy.** The process exit status and the envelope's `exit_code` are the same stable small integer, and `CLIResultStatus` (§10) is a deterministic projection of it:

   | Code | `CLIResultStatus` | Required meaning |
   |---:|---|---|
   | 0 | `ok` | Success, including a retained generation, another no-op result, or idempotent `init`. |
   | 1 | `failed` | Unexpected internal failure. This is the only class for an otherwise unclassified error and its diagnostic is secret-safe. |
   | 2 | `failed` | Invalid command, usage, selector, or input, including an unresolved naive datetime, a nonexistent or ambiguous local time, or another boundary-validation rejection. |
   | 3 | `failed` | Missing, invalid, or undiscoverable required workspace. |
   | 4 | `failed` | Incompatible or unrecognized schema under §12.14, including a partial initialization or `init` version mismatch. |
   | 5 | `failed` | Concurrency conflict in the §8.1 `workspace_busy` class. |
   | 6 | `failed` | Provider or transport failure under §15.10, including capability mismatch or budget/context-overflow preflight refusal. |
   | 7 | `failed` | Validation or integrity failure, including §15.1 invalid-after-retry, §12 rule 10, hydration, or migration validation failure. |
   | 8 | `failed` | Incomplete managed-output cleanup or privacy deletion at non-cancelled completion, including `deletion_incomplete` and any reported residual path. |
   | 9 | `cancelled` | User interruption under §15.10/§13.13. |
   | 10 | `blocked` | A completed semantic result whose verifier or consumer gate does not pass: non-passing `assess verify` or `bullets verify` findings, or export refused by a §16.11 allowlist. |

   Code 10 is a successful semantic computation, not an operational-failure class: its complete findings are retained and its completed verifier `processing_runs` row is not marked failed. A handled user interrupt takes code 9 precedence over every simultaneously observed class, including incomplete cleanup after an already committed deletion; committed effects and every known `residual_path` remain reported in the cancelled envelope. Code 8 applies when the command reaches a non-cancelled completion with required cleanup incomplete. Exit codes are configuration-independent; a recognized class never collapses into code 1. Existing codes never change meaning or number, and a new class appends a new code.
5. **Machine-readable result envelope and output channels.** With `--json`, stdout contains the UTF-8 serialization of exactly one versioned result-envelope object, optionally followed by one final newline, and no banner, progress line, diagnostic, prompt, or other byte. The outer object and every nested object apply §11's strict validation, string-hygiene, and `extra = forbid` policy. Every field below is present; inapplicable scalar values are `null` and inapplicable collections are empty. Version 1 is closed: adding, removing, retyping, or changing the meaning of a field requires a new `envelope_version`, and an implementation fails closed on an unsupported version.

   | Field | Version-1 type and meaning |
   |---|---|
   | `envelope_version` | Integer `1`. |
   | `command` | Canonical §14 command path without arguments, or `null` when parsing did not resolve a command. |
   | `status` | Canonical `CLIResultStatus`, constrained by the exit-code table above. |
   | `exit_code` | Integer process exit status from the table above. |
   | `diagnostic_class` | Stable non-empty machine code, or `null` if and only if `exit_code = 0`; code 10 uses a gate-specific blocking class. These codes are open for append-only extension like §12.13 `failure_code`, not a §10 enum. |
   | `workspace` | Canonical real discovered or overridden workspace-root path, the canonical `init` target, or `null` when no root was established. |
   | `affected_ids` | Closed object with exactly `created`, `superseded`, and `deleted`; each is a list of closed `{entity_type: str, ids: list[str]}` groups so per-table IDs remain typed by class. |
   | `generation_ids` | Duplicate-free produced or invalidated §12 rule 13 generation IDs. |
   | `run_ids` | Duplicate-free §12.13 processing-run IDs created or directly inspected by the command. |
   | `invalidated_views` | Complete list of closed `{scope: AssessmentScope, scope_target: str | null, snapshot_id: str, regeneration_command: str}` §13.13 rule 9 reports; the command is executable and POSIX-shell-quoted. |
   | `invalidated_branches` | Complete list of closed `{name: str, job_description_id: str, former_view: {scope: AssessmentScope, scope_target: str | null, snapshot_id: str}, regeneration_command_shape: str}` §13.13 rule 9 reports. |
   | `findings` | Complete §11.14 `VerificationFinding` values produced or directly inspected by the command; otherwise empty. |
   | `residual_paths` | Complete canonical managed paths whose required cleanup or deletion did not complete. |
   | `warnings` | Values using §15.1's closed `ContractWarning` shape; each message is a secret-safe owner-facing explanation surfaced by the command and contains no source quotation. |
   | `retry` | Closed `{command: str}` containing the documented, POSIX-shell-quoted executable retry command from §13.13/§14.12 when one exists; otherwise `null`. |
   | `result` | Closed primary-result object discriminated by `command` under the table below, or `null` when the standard envelope fields carry the complete result. A free-form object is invalid. |

   The `command` value selects exactly one result schema; every object and projection below is closed and uses the referenced §10/§11/§12 field types:

   | Command | Exact `result` payload |
   |---|---|
   | `init`, `db status`, `db migrate` | `{schema: {stored_version: int | null, supported_version: int, recognized: bool, compatible: bool, migration_path_available: bool | null, managed_backup_path: str | null}}`. |
   | `import ephemeris`, `import atlas`, `import github` | `{counts: {accepted: int, duplicate: int, conflict: int, rejected: int}, records: {accepted: list[ImportRecordResult], duplicate: list[ImportRecordResult], conflict: list[ImportRecordResult], rejected: list[ImportRecordResult]}}`, where `ImportRecordResult` is the closed `{record_number: int, source_record_id: str | null, raw_log_id: str | null}` §19.4 projection; `raw_log_id` is non-null exactly for a record created by the committed import. |
   | `logs list` | `{logs: list[{id, recorded_at, entry_type, source_type, occurred, project, corrects_log_id}]}`, the `raw_text`-free §11.2 inspection projection; `raw_text`, `metadata`, and `external_ref` are absent. |
   | `logs delete` | `{selected_log: {id, recorded_at, entry_type, source_type, occurred, project, external_ref, corrects_log_id}}`, the captured pre-deletion §11.2 report required by §14.11; `raw_text` and `metadata` are absent. |
   | `facts list`, `facts show` | `{facts: list[ExperienceFact]}` using complete §11.4 values; a successful `show` result contains exactly one. |
   | `detections generate` | `{gaps: list[GapQuestion], contradictions: list[Contradiction]}` containing both complete retained or replacement §14.7 result sets. |
   | `gaps list` | `{gaps: list[GapQuestion]}` using complete §11.10 values. |
   | `contradictions list`, `contradictions show` | `{contradictions: list[Contradiction]}` using complete §11.9 values; a successful `show` result contains exactly one. |
   | `signals list` | `{signals: list[SelfSignal]}` using complete §11.5 values. |
   | `assess list` | `{snapshots: list[{id, scope, scope_target, verification_status, created_at}]}`, exactly the §14.9 discovery projection. |
   | `assess show` | `{snapshot: AssessmentSnapshot, claims: list[SelfClaim], gaps: list[GapQuestion], contradictions: list[Contradiction]}` using the complete §11 values reached through the snapshot's claim membership (`SelfClaim.snapshot_id`, claim-ID order) and its typed gap/contradiction references. |
   | `jd list` (§14.15) | `{job_descriptions: list[{id, created_at, title, company}]}`. |
   | `jd show` (§14.15) | `{job_description: {id, created_at, title, company, parsed}}`, the §11.11 `raw_text`-free inspection projection; `raw_text` is absent and `parsed` is the complete §11.13 value. |
   | `jd delete` (§14.15) | `{selected_job_description: {id, created_at, title, company}, purged_branches: list[{id, name}], removed_managed_paths: list[str]}`; `purged_branches` contains every current and historical dependent branch captured before deletion, while residual paths use the envelope's top-level `residual_paths`. |
   | `runs list` | `{runs: list[{id, stage, parent_run_id, started_at, finished_at, status}]}`, exactly the §14.13 list projection from §12.13. |
   | `runs show` | `{run: <complete §12.13 row with metadata_json carried as its stored JSON TEXT string>, calls: list[<complete §12.15 row>]}`; every field is a closed scalar — the string-typed `metadata_json` keeps the projection `extra = forbid`-validatable while §12.13's content prohibition governs what the stored text may contain — and its §11.14 rows use the top-level `findings` field. |
   | `bullets generate`, `bullets verify` | `null`; their complete primary result is carried by the standard `affected_ids`, `generation_ids`, `run_ids`, and `findings` fields as applicable. |
   | `export assessment`, `bullets export` | `{manifest_path: str, managed_paths: list[str]}`. `manifest_path` is the final ID-keyed `manifest.json`; `managed_paths` is the complete deterministic list containing that manifest and every fixed §13.12 member path. The result is emitted only after §13.14 revalidates the matching manifest, exact member set, current render-input hash, source graph, and member hashes; a missing, invalid, stale, or mismatched manifest yields no partial result. |

   Subject to rule 4's cancellation precedence, a complete §19.4 import result with `counts.conflict > 0` uses existing exit class 7; otherwise `counts.rejected > 0` uses existing exit class 2; and an accepted/duplicate-only result uses exit class 0. Conflict therefore takes precedence over acquisition rejection when one classified batch contains both, without adding an exit class.

   Except for a row that explicitly declares `null` as its complete success schema, a command with an object result uses `result = null` only when it fails or is cancelled before a complete primary result exists; it never emits a partial result object. A nonzero completed report such as incompatible `db status` or a fully classified atomic import rejection still carries its complete typed result. Every unlisted V1 command uses `result = null`: its primary result is already complete in `affected_ids`, `generation_ids`, `run_ids`, invalidation reports, `findings`, `residual_paths`, `warnings`, and `retry`. Adding or renaming a command requires a row declaring its closed object or `null` success schema; widening an object projection requires declaring the change in §14 and incrementing `envelope_version` when version 1 is already implemented.

   IDs, entity groups, paths, findings, views, branches, and result records are duplicate-free and deterministically ordered by their stable class and identity; §19.4 import records use their input `record_number` as that identity, so repeated source identities remain distinct report entries. Completeness-required result lists — verifier `findings` for every current claim or bullet, §13.13 invalidation reports, §19.4 import classification lists, `affected_ids`, and residual paths — are never truncated: envelope serialization to local stdout is not one of §11's bounded provider, acquisition, response, or hydration boundaries, so neither §11's general per-list cap nor its narrower findings/warnings cap applies to it, and a view or branch with more than 100 current claims or bullets or an import with more than 100 established records still receives its complete result. Each individual §15 response and every other §11 boundary keeps its caps unchanged. The envelope never contains a secret, credential, prompt, `raw_text`, or undeclared source content. Content-bearing values are IDs and machine codes except for the closed command result projections, warnings, verifier findings, managed paths, and view/branch regeneration reports already surfaced by their owning §13/§14 contracts. Human mode may format the primary result as text on stdout. In both modes every diagnostic and progress event goes to stderr; `--quiet` may suppress progress but not the result or a diagnostic required to explain a nonzero exit. The §8.1 public-diagnostic rule applies, and internal logs and exception reports are secret-safe and contain no private source text. Writing the envelope to local stdout is not egress under §29.
6. **Interruption and confirmation semantics.** On a user interrupt every command exits with code 9 and `status = cancelled`. §15.10 owns in-flight LLM-call cancellation, §13.13 owns lifecycle atomicity, and §8.1 owns lock and transaction release: the in-flight transaction rolls back and no partial current generation becomes visible, while a correction, deletion, cleanup result, or other lifecycle boundary already committed under §13.13 remains committed and is reported rather than restored. The confirmation requirements are exactly those in rule 3; neither configuration nor an environment variable can imply consent.
7. **Inspection-surface completeness.** The operable V1 lifecycle-inspection surface is `db status`; `logs list`; `facts list` and `facts show`; `gaps list`; `contradictions list` and `contradictions show`; `signals list`; `assess list` and `assess show`; `jd list` and `jd show` (§14.15); and `runs list` and `runs show`. Evidence-item listing, resume-branch or bullet listing, historical-generation browsing beyond `runs show`, and parsed-requirement dumps beyond `jd show` are explicitly deferred read-only additions. They add no V1 mutation or decision surface.
8. **Time input resolution.** The selected workspace's `[workspace].timezone` IANA name in `.exp2res/config.toml` (§29.2) is the sole authority for interpreting local time. This setting has no CLI flag, `EXP2RES_*` environment variable, or built-in default representation, and no command reads the ambient OS timezone or locale when producing a persisted value. A configured value is required at the first use of a local-time feature; a missing, empty, or unrecognized value fails that operation closed without a silent default. `log today` and retrospective capture resolve `today` and named period anchors in the workspace timezone, and persisted `OccurredAt` bounds carry the resulting offset. A naive datetime received by the CLI is resolved in the workspace timezone before §11 model construction; the §11 model boundary still rejects every naive value that reaches it. A naive local time in a daylight-saving gap or fold fails closed with guidance to supply an explicit offset, and Exp2Res never silently chooses a fold. The tzdata version is not pinned: resolution uses the rules available to the build at resolution time, while the persisted offset makes the stored instant independent of later tzdata changes.

## §14.15 Manage Job Descriptions

```bash
exp2res jd list
exp2res jd show --jd jd_001
exp2res jd delete --jd jd_001
```

`jd list` and `jd show` are read-only local inspection. Their exact projections, which never expose `JobDescription.raw_text`, are §14.14 rule 5's rows. Neither command invokes a provider or serializes content into a prompt.

`jd delete` is the owner's per-job-description destructive privacy operation and uses §14.14 rule 3 confirmation semantics: `--yes` supplies consent, otherwise a TTY confirmation is required and non-interactive use fails closed. It invokes §13.13 rule 10's dependent purge. The command reports the selected deleted job description, every purged branch with its ID and name, and every removed managed path through the closed result in §14.14 rule 5. Any residual managed path is also reported in `residual_paths` with `deletion_incomplete`, never success; the database deletion remains committed under §13.13 rule 6.

## §14.16 Purge Workspace

```bash
exp2res workspace purge
exp2res workspace purge --yes
```

`workspace purge` is the whole-workspace destructive privacy operation and uses §14.14 rule 3 confirmation semantics. In one owner-initiated flow it removes every managed source, derived, and execution-content class: all raw logs and evidence; every current and historical derived generation, including fact sources and verification findings; all job descriptions and parsed requirements; all `processing_runs` and `llm_calls` telemetry; every captured ID-keyed managed `out/` set whether its manifest is valid or not; every §13.14 candidate or rollback sibling; every §12.14 migration backup; live content in SQLite WAL/SHM sidecars through the §8.1 checkpoint discipline; and every other operation-owned temporary output created by Exp2Res inside the managed workspace. `config.toml` is the sole retained data-bearing control-plane file: it is owner-authored configuration, contains neither source content nor provider credential values under §29.2, and remains with the initialized workspace.

The flow first enumerates and attempts the managed-path removals under §13.13 rule 6 and §13.14's no-follow containment contract, then clears all business and telemetry rows in one referentially ordered transaction and replaces the prior `schema_meta` history with exactly one fresh row for the running build's current schema version and application version. This is the sole whole-workspace lifecycle exception to §12.14's append-only history: purge establishes a fresh empty database history rather than migrating or editing retained history. The command itself leaves no `processing_runs` or `llm_calls` row. Purge resets no entity-ID counter and never authorizes intentional ID reuse: later allocation remains collision-resistant and independent of surviving row counts under §12 rule 11. On success, the `.exp2res/` directory, its empty current-version database, its retained `config.toml`, and empty managed directory roots remain initialized; the owner may remove the workspace directory manually when even configuration and infrastructure should disappear.

After the database transaction commits, the flow applies §8.1's checkpoint, `VACUUM`, and final-checkpoint erasure sequence. Database erasure remains committed if managed-path removal or an erasure step fails; every residual path, including a managed symlink that was not followed, is reported as `deletion_incomplete`, never success. No rebuild follows because no source state remains. This differs from `logs delete`, which deletes one raw record, globally resets derived state, and rebuilds Stages 3–5, and from `jd delete`, which purges only one vacancy's dependent resume state. Manual recursive directory removal performs no application-controlled secure-delete, checkpoint, or `VACUUM` pass before unlinking; although path entries may disappear, bytes formerly held in database free pages or SQLite WAL/SHM sidecars may remain recoverable in filesystem or storage remnants. §29.6 defines the limits of either operation.

---
