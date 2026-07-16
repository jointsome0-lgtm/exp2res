## §29. Security and Privacy

## §29.1 Local Canonical Boundary and Private Default

The owner-controlled local workspace is Exp2Res's only canonical persistence domain. SQLite is authoritative for owner-controlled `RawLog` records, evidence, and every derived entity. Owner-supplied files remain source material at their supplied paths, and managed `out/` files remain local derived projections rather than a second truth model. Provider-retained prompts or responses never become canonical Exp2Res state.

Private-by-default operation sends nothing anywhere on system initiative. Exp2Res has no outbound telemetry, background sync, auto-push, update check, implicit cloud persistence, deferred model call, or other autonomous egress. The `processing_runs` and `llm_calls` telemetry in §12.13/§12.15 is local SQLite execution history and never authorizes network telemetry.

## §29.2 Authorized Outward Transit and Provider Trust

Only a foreground, user-initiated §14 action that invokes a pipeline stage may authorize outward transit, and only for the synchronous §15 contract calls belonging to that run. Correction, owner-deletion, and recompute actions in §14.4, §14.11, and §14.12 carry that authorization only into the Stage 3–5 calls they synchronously orchestrate through §13.13; Stage 6–7 calls require their own §14.9 actions, and the lifecycle service gains no independent call authority. The one invalid-response retry allowed by §15.1 and the bounded transport retries allowed by §15.10 remain inside the same foreground action; neither may be queued, deferred, resumed, or adopted later in the background. Outside one of the eight invoked §15 contracts, no component, importer, renderer, export path, or lifecycle service may call an LLM or any network endpoint. §19 importers consume user-supplied local payloads under §14.5; source acquisition is outside Exp2Res.

An agent-backed adapter (§15.12) executes its provider calls through the isolated agent-runner protocol: the spawned agent runtime is part of the same foreground action, gains no independent call authority, can read nothing beyond its per-invocation contract workspace, and holds network access only for the duration of that authorized transit window — which version 1 does not endpoint-filter. The residual this section's closing inventory names is therefore two-part: the invocation's own declared input, which the action was already authorized to transmit, and the adapter's declared authentication material, which §15.12 rule 2 necessarily binds into the sandbox for provider transit; rules 1–6 leave nothing else readable, and suspected credential exposure is recovered by rotation under §29.4's lifecycle rules.

Serving a §30 view only on the local host's loopback interface and returning it to a local browser is local presentation, not egress or a ninth model-call site; every view-triggered workflow that reaches §15 remains an explicit foreground user-initiated §14 action under the same authority and confirmation rules.

Workspace timezone, provider, and ignore selection live in the local `.exp2res/config.toml` created by §14.1; they are configuration, not commands:

```toml
[workspace]
timezone = "<IANA name>"

[llm]
provider = "<user-selected-provider>"
api_key_env = "ANTHROPIC_API_KEY"

[privacy]
ignore_paths = []
```

Provider credential values are never stored in `config.toml` or anywhere else in the workspace. The `[llm]` section may contain provider and §15.10 budget configuration, but each credential slot may use only one reference form: an environment-variable name such as `api_key_env = "ANTHROPIC_API_KEY"` or a keyring entry name such as `api_key_keyring = "exp2res/anthropic"`. The adapter resolves that reference only at call time; the environment or keyring value remains a transport-only adapter value under §29.4 and never enters the §14.14 configuration-precedence chain. A missing or ambiguous reference fails the outward call closed.

At configuration load, the service applies every supported adapter's registered §29.4 credential and token classifiers to every configured value. If a value is recognized as a literal credential rather than a reference name, loading fails closed before business I/O with a non-secret diagnostic; the value is neither echoed nor copied into telemetry. Under the POSIX-only V1 runtime, Exp2Res creates `.exp2res/` and each managed subdirectory with mode `0700` and each managed file with mode `0600`, without relying on a permissive process umask.

At a §14.14 local-time feature boundary, `workspace.timezone` is validated against the IANA tzdata database available to the build. A missing, empty, or unrecognized name fails the operation closed; §14.14 owns all interpretation semantics and defines no silent default.

The provider must be selected explicitly before the first outward call. Exp2Res defines no default provider, discovery request, or automatic fallback, and remains provider-agnostic; a local provider follows the same typed boundary even when no network transit occurs. §15.10 capability validation applies only to that selected provider/model and reads adapter declarations plus local configuration; it is not a network probe, does not contact another provider, and grants no new egress class. User ignore entries extend the mandatory exclusions in §29.4 and cannot weaken them.

Prompts and responses are subject to the selected provider's retention, access, and training policies. Choosing a provider is choosing who may see every data class in §29.3. Exp2Res cannot guarantee provider-side confidentiality or erase provider-retained copies through local owner deletion; that exposure is an accepted residual risk, not implicit cloud persistence authorized to Exp2Res.

## §29.3 Exhaustive LLM Transmission Surface

The following eight contracts are the complete model-call surface. The selected provider receives the fixed contract instructions and the exact declared typed input for that invocation; a data class listed here is transmitted only when the owning stage selects it under the contract and §13.

| Contract | Personal or third-party data visible to the selected provider |
|---|---|
| §15.2 fact extractor | Effective correction-lineage `RawLog` objects and their linked complete `EvidenceItem` objects: raw memories, gap answers, imported text, burnout-grade or other sensitive `raw_text`, dates, projects, source metadata, external references, evidence summaries, and permitted path/URI values. `displaced_support_items` additionally expose the §13.3 rule 10 prose-free descriptor projection of displaced-record non-`manual_claim` items: item and raw-log IDs, `strength`, and permitted locator `uri`/`path` values; displaced raw text, `title`, `summary`, `created_at`, and `metadata` do not transit. |
| §15.3 signal extractor | Derived experience facts, displacement-aware linked evidence items, and contradictions, including projects, roles, companies, dates, skills, confidence, evidence values, and provenance IDs. Items linked to displaced records transit only as §13.3 rule 10 prose-free descriptors; complete non-displaced items may expose summaries, metadata, and path/URI values. It receives no raw logs. |
| §15.4 assessment writer | Assessment scope and target plus derived facts, signals, gaps/questions, and contradictions: the personal patterns, uncertainties, conflicts, and evidence from which a self-assessment is authored. |
| §15.5 assessment verifier | A candidate self-claim with its snapshot's scope and target, the view's complete current signal and fact sets, and its exact §15.5 provenance closure: source signals, closure facts, their displacement-aware evidence context, and only the non-displaced retained raw logs reached through them. Displaced-record items transit only as §13.3 rule 10 prose-free descriptors, and displaced `RawLog` objects never transit; non-displaced logs may still expose raw personal or burnout-grade text. |
| §15.6 resume writer | Branch/scope context; job-description ID, title, company, and complete `ParsedJD`; selected facts with displacement-aware linked evidence and only non-displaced raw logs; and supported self-assessment claims. Displaced-record items transit only as §13.3 rule 10 prose-free descriptors, and displaced `RawLog` objects never transit. This may include raw source text from non-displaced logs, derived self-assessment, and third-party demand data. |
| §15.7 resume verifier | A candidate resume bullet, its complete source facts with their displacement-aware evidence/raw-log provenance IDs, only non-displaced source-log objects, its self-claims, and the branch job-description ID and complete `ParsedJD`. Under §13.3 rule 10, displaced-record items can transit only as prose-free descriptors; §15.7 transmits no `EvidenceItem` object at all, and displaced `RawLog` objects never transit, so displaced item/log identities remain opaque fact provenance references. This may include raw source text from non-displaced logs, derived self-assessment, and third-party demand data. |
| §15.8 gap and contradiction detector | Complete current facts and effective-lineage evidence, including factless `RawLog.raw_text`; raw memories, gap answers, imported text, and burnout-grade text may therefore transit. |
| §15.9 job-description parser | Third-party `JobDescription.raw_text` only, including any company, contact, or other personal data the supplied vacancy contains. No local record ID transits: no job-description entity exists at call time, and Stage 8 assigns the ID only after the response validates (§15.9). |

No invocation receives the full database, ambient provider conversation history, another contract's inputs, or a persistent remote assistant, file store, vector store, or cache created by Exp2Res. Adding a ninth call site, adding a network-capable tool, or widening any row beyond its declared §15 input is a weakening governed by §29.7.

## §29.4 Secret, Ignore-Path, and Prompt Isolation

V1 supports local paths under POSIX semantics on Linux and macOS only; Windows runtime and path semantics are outside the V1 support boundary. At acquisition and at the pre-serialization re-check below, a Windows drive-letter path such as `C:\…` or `C:/…`, a UNC path such as `\\server\…`, or any backslash-separated path is unsupported and fails closed without reinterpretation. A local `file:` URI must resolve to a POSIX path.

The prompt composer may serialize only the fixed instructions and typed input fields declared by the invoked §15 contract. It has no access to environment dumps, shell or free command output, directory listings, filesystem sweeps, unrelated database rows, or non-selected file content. Provider credentials and tokens are transport-only adapter values: they never enter a prompt, `processing_runs` or `llm_calls` telemetry, generated warnings, or diagnostic text.

For an agent-backed adapter, §15.12 extends this isolation to the spawned agent runtime itself: the runtime executes inside an OS sandbox in which only the per-invocation contract workspace, the runtime's own binaries, transit essentials, and the adapter's declared authentication material exist, so ambient repository files, user-level agent rules and configuration, the Exp2Res workspace and its database, the user home profile, and the parent environment are structurally unreadable rather than merely un-serialized. A runtime-level read-only mode is not read confinement; the declaration-checked sandbox is, and its source of truth is §21.50's effectiveness canary, not any prose enumeration of host binds — an unavailable or ineffective wrapper fails §15.12 rule 8's two-half preflight closed before transport.

This gate governs the source-acquisition channel. Before any Exp2Res local-file reader — including capture, import, job-description addition, and evidence dereference — opens a `path` or local-file `uri` as source material, it resolves the canonical real path, including symlinks, and applies the mandatory deny set plus the user's `privacy.ignore_paths`. Mandatory-deny and user-ignore comparisons are byte-wise over that canonical real path. Canonicalization is not guaranteed to rewrite a supplied component to its on-disk spelling, so on a volume whose name lookup is case-insensitive the same comparisons are additionally applied under the locale-independent case fold: a case-variant spelling such as `.ENV` matches the mandatory `.env` entry and is denied. Case-insensitive lookup can therefore only narrow acquisition relative to a case-sensitive volume, never widen it. Managed-output alias prevention is independent of those source-path comparisons: §13.14 derives lowercase-ASCII single-component directory keys only from opaque service IDs, admits no user string into a path, and applies canonical containment plus no-follow semantics to every managed filesystem operation. The service's own reads of the two §14.1 workspace internals — `.exp2res/config.toml` for configuration and the SQLite database file for storage — are internal service I/O outside this gate: neither is reachable as a `path`, `uri`, or payload locator (the deny set blocks exactly that), and neither read serializes file content into a prompt, so the first config load that selects the provider and the ignore patterns is well-defined rather than circular. A file is selected only when its exact path is explicitly supplied as a source-path argument to the current §14 action, including §14.2 `--file`, or when a relative locator inside an imported payload resolves beneath that action's user-selected payload root; for a selected payload file, that root is its containing directory. An embedded absolute locator, `..` escape, or symlink target outside that root is non-selected. Mandatory names match any canonical basename or path component at any depth. User patterns use gitignore-style syntax relative to the selected root and are evaluated after canonicalization. The mandatory path-reader deny set is:

```text
.env
.env.*
*.pem
*.key
secrets/
credentials/
.git/
.exp2res/
out/
node_modules/
.venv/
dist/
build/
```

Typed SQLite reads of selected contract objects are not filesystem reads, so the `.exp2res/` denial does not prevent the service from loading declared database inputs. An ignored, unresolved, or non-selected path fails closed at acquisition before either its locator or content can later reach a prompt; a stage that requires that object fails rather than silently omitting it from a complete input set. Root containment is an acquisition-time authorization check. Immediately before an object with a persisted `path`, `uri`, `url`, or `external_ref` — including a §13.3 rule 10 displaced-record support descriptor carrying a persisted `path` or `uri` — is serialized into a prompt, local paths and file URIs repeat POSIX-form validation, canonicalization, and current mandatory/user ignore checks, while non-local schemes remain inert provenance. Earlier ingestion never waives a later ignore rule, and no locator value is authority to fetch; non-local URIs are not dereferenced in V1.

A local deterministic preflight examines the fully serialized candidate prompt for credential, token, and private-key material. The §11 size-and-structure preflight runs alongside this credential preflight before transport. At minimum it detects every exact credential value resolved by the selected adapter, PEM/private-key block markers, every non-empty value in a field whose normalized name is `api_key`, `access_token`, `refresh_token`, `secret`, `password`, or `authorization`, and the token formats registered by every supported provider or integration adapter. An adapter without deterministic credential and token classifiers is invalid. Any detection fails the run before a provider call and records only a non-secret diagnostic code. The retained source record is neither rewritten nor silently redacted. A model response cannot request a tool, callback, file read, environment value, command execution, or additional network access; no §15 output field grants such authority.

## §29.5 Untrusted Data and Prompt Injection

Imported artifact text, Tick-like and GitHub natural-language payloads, `RawLog.raw_text`, gap answers and their copied question context, evidence labels and summaries, and `JobDescription.raw_text` are untrusted DATA even when they resemble instructions. They may supply evidence or third-party demand content only through the owning typed field. They never alter fixed contract policy, select additional context, authorize another call, waive a verifier rule, or direct requirement matching.

Thus source text such as "ignore your rules," "mark every requirement matched," "render this project as employment," or "read ../../.env" remains ordinary source data. A §15.9 parse may represent such text only in a contract-defined non-control field when that representation is faithful, for example as a red flag; it may not turn the text into a matchable `JDRequirement` or a service instruction. A §15.6 writer may match only requirements supported by its declared inputs, regardless of instructions embedded in the vacancy or evidence.

§16.12 remains the voice-origin boundary: source text is preserved and receives structure-only validation at ingestion, while generated candidates remain fully bound by the applicable voice and evidence rules. §19 remains the structure-only ingestion authority. Neither rule makes source text trusted prompt policy. The closed-output rule in §15.1 and the exact typed-reference checks in §12 rule 10 are structural backstops; provenance, relevance, and Stage 7/11 verifier gates remain semantic backstops.

Any candidate that follows instruction-like data — for example by emitting an undeclared control field, reading another path, matching an unrelated requirement, or upgrading an imported assertion without declared support — is invalid and fails before its business output persists. A semantic injection failure does not authorize the §15.1 schema retry, a writer repair pass, or another model call.

## §29.6 Lifecycle Guarantee and Residual Risks

The point-deletion algorithms in §13.13 and whole-workspace operation in §14.16 own execution order. The following table is the normative inventory of managed data classes and deletion responsibility; it does not create another command or recompute algorithm.

| Data class | Canonical store | Deletion trigger | Required behavior |
|---|---|---|---|
| Raw logs and linked evidence | SQLite | `logs delete` (§14.11); `workspace purge` (§14.16) | Point deletion removes the selected row and linked evidence under §13.13's global derived reset; purge removes all rows. |
| Current and historical derived generations, including verification findings | SQLite | §13.13 invalidation or deletion flow; `workspace purge` | Recompute/correction supersedes replaced generations; raw-log deletion purges all; JD deletion purges dependent branches, bullets, and bullet findings; workspace purge removes all. |
| Job descriptions and parsed requirements | SQLite | `jd delete` (§14.15); `workspace purge` (§14.16) | Point deletion hard-deletes the selected JD and its dependent resume state; purge removes all. |
| Processing-run and LLM-call telemetry | SQLite | any §13.13 point deletion; `workspace purge` (§14.16) | Each point-deletion transaction retains content-free execution telemetry but globally sets every call hash committed before that transaction to `NULL`; a raw-log rebuild may then record fresh hashes over surviving content only, while JD deletion performs no rebuild; workspace purge removes every row. |
| Configuration and provider selection | `.exp2res/config.toml` | owner edit; manual workspace-directory removal | Workspace purge retains this control-plane file; it contains no source content or literal credential value. |
| Provider credentials | environment or OS keyring, outside the workspace | owner/platform/provider credential lifecycle | Exp2Res neither stores nor deletes the credential value. |
| Managed exports | `out/` | invalidation, point deletion, or workspace purge | §13.14 identifies current ID-keyed sets by their closed manifest; a lifecycle flow removes each captured entity-ID path with canonical no-follow containment even when its manifest is invalid, or reports that path as residual. No missing, invalid, stale, or hash-mismatched manifest is current output. |
| Migration backups | `.exp2res/backup/` | §13.13 deletion flows; `workspace purge` (§14.16) | The owning deletion flow removes each backup or reports it as residual. |
| SQLite WAL/SHM sidecars | adjacent to `.exp2res/exp2res.sqlite` | §8.1 after each destructive flow | Required checkpoints truncate live WAL content; purge additionally vacuums and checkpoints again. An empty or SQLite-maintained sidecar may remain while a reader is connected; incomplete truncation is residual. |
| Managed temporary outputs | §13.14 candidate/rollback siblings or another operation-owned temporary path inside the workspace | next-writer preamble; owning operation; `workspace purge` (§14.16) | §13.14 deterministically removes, restores, or reports abandoned publication siblings in the next-writer preamble; an unreconciled sibling blocks managed-output publication but not a cleanup-tolerant database lifecycle, and the owning operation or purge removes every other remainder or reports it as residual, always without following a symlink. |
| Per-invocation runner contract workspaces | system temporary location, outside the workspace (§15.12) | owning invocation, on every completion path | §15.12 rule 5 deletes the contract workspace on success, validation failure, transport failure, and cancellation. A hard-crash leftover is an inert owner-only-mode directory with a recognizable prefix containing only that invocation's serialized contract input, schema, any final-message output, and any runtime session artifacts §15.12 rule 5 confines there — so it can hold prompt and response material; it lies outside every backup, scan, and purge inventory walk and is removed by the owner or the platform's temporary-file cleanup, never by the next-writer preamble. |

Point-retained telemetry is not identifier-free: run IDs, opaque internal entity IDs, and the opaque `provider_request_id` transport correlation may remain. Under §12.13, §12.15, and §15.10, none is a stable identifier of a person or content-derived value; there is no telemetry field for an account ID, user ID, email address, source path, raw text, or derived prose. A provider adapter that encodes one of those values into its request correlation is non-conforming.

The following risks remain explicit:

1. The selected provider may retain or expose transmitted prompts and responses after §13.13 or §14.16 removes local managed data. Provider choice accepts that provider-controlled risk.
2. A structurally valid imported artifact may be false or malicious, and an LLM may return a schema-valid semantic error. Provenance, evidence strength, replacement generations, and verifier gates limit unsupported promotion but do not authenticate every external assertion or make the model infallible.
3. Owner-supplied source files and copies of exports or backups outside the managed workspace remain outside Exp2Res's deletion authority.
4. `secure_delete` is a SQLite page-level logical overwrite, WAL checkpointing truncates the live sidecar, and `VACUUM` rewrites the live main database; even together they do not prove physical erasure from filesystem snapshots or journals, SSD wear-leveling cells, backup media, or OS swap.
5. §15.12's sandbox does not filter network egress by endpoint: an agent runtime that were successfully injected despite the runner's controls could transmit its own contract input — or the adapter's declared provider credential, which §15.12 rule 2 necessarily binds into the sandbox for transit — to a non-provider endpoint during its authorized network window. Rules 1–6 of §15.12 bound that exposure to exactly that input and that single credential, with no other host data readable; suspected credential exposure is recovered by rotation under §29.4, and a hard crash may leave one inert contract workspace in the system temporary location until removed.

These residual risks do not authorize secret transmission, autonomous egress, instruction-following from source data, or a new network path; those remain fail-closed requirements.

## §29.7 Change Control

Weakening any normative rule in §29, expanding the §29.3 transmission table, relaxing a mandatory ignore or secret boundary, or adding another LLM or network path requires a Decision Log entry in the same change.

---
