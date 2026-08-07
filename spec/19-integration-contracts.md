## §19. Integration Contracts

- Every importer validates the payload's keys, types, closed-enum mappings, and required identifiers.
- Importer validation includes the boundary limits and text-hygiene rules in §11's Model validation policy.
- A payload's natural-language values remain system-of-record source voice under §16.12: §19.1 activity `text`, §19.2 snapshot `text` and `summary`, GitHub `message`, and local imported-document text are preserved and structure-only scanned at ingestion.
- A voice rule may constrain a later Exp2Res-authored fact, claim, report sentence, or resume bullet that uses this material.
- A voice rule may never reject, rewrite, or block the imported value itself because of its wording.
- Imported source identifiers — each source contract's own record identity and GitHub `commit_sha`/`repo` — remain provenance values in `RawLog.external_ref` or `RawLog.metadata`.
- Those imported source identifiers must never become local entity `id` values.
- Every local `path` or `file:` URI value carried by an import payload, including Atlas `path`, is governed by §29.4's POSIX-only acquisition and pre-serialization rules.
- The JSON objects in §19.1–§19.3 are the complete accepted record shapes: each importer accepts its own source's typed record directly, with no wrapping envelope, shared version field, or cross-source discriminator.
- Each source subsection owns its record shape and names the field that carries its stable source identity; §19.4 owns only what every importer shares — identity comparison, duplicate handling, per-record processing, and result reporting.

## §19.1 Activity-Domain Evidence Contract

This source contract declares the closed JSON object below as the complete record the `ephemeris` importer accepts. It is the source-agnostic activity-domain evidence intake Exp2Res accepts, not a Tick-like wire contract.

```json
{
  "source": "ephemeris",
  "record_id": "ephemeris:event:2026-07-03T10:00:00+02:00:verifier-gate",
  "domain": "activity",
  "occurred": {
    "start": "2026-07-03T10:00:00+02:00",
    "end": null,
    "precision": "exact_datetime",
    "confidence": "high"
  },
  "project": "Exp2Res",
  "text": "Worked on verifier-gate design."
}
```

- All six fields are required:
  - `source` must equal the literal `ephemeris`.
  - `record_id` is this contract's §19.4 source identity: the adapter's stable, non-empty identifier for the source record.
  - `domain` must equal `activity`.
  - `occurred` is a complete §11.1 value.
  - `project` is a non-empty source project label.
  - `text` is non-empty source voice.
- The record is closed and has no pass-through metadata or knowledge-state field.
- Diary/daily notes, verbal work notes, and focus/time aggregates may enter only when the source explicitly reports activity.
- A plan or learning assertion does not establish completed activity merely by appearing in `text`.
- A learning record's structured knowledge-state, trail, or evidence-reference payload is invalid here.
- Only a separately represented time/activity aspect may enter this contract; knowledge state routes through §19.2.
- Text that mentions learning still carries only `imported_activity_event` scope and cannot establish §9.4 knowledge-state attribution on an Atlas scale.

Adapter and time-field ownership:

- The selfos-side adapter owns mapping Tick-like's events-replay records (`{timestamp, type, payload_version, payload}`) and calendar series into this record, including source-type interpretation and the §5.4 distinction between source recording time and described occurrence time.
- The adapter maps the stable upstream identity to `record_id`.
- A source timestamp that records only capture/replay time never populates `occurred`.
- A timestamp whose upstream semantics place the described activity may contribute to `occurred`.
- Exp2Res assigns `RawLog.recorded_at` when the import enters the workspace, independently of `occurred`.
- A source-only recording timestamp remains adapter-side provenance rather than being relabeled as either Exp2Res time field.
- No field or accepted value in this contract depends on Tick-like's upstream schema.

Import behavior:

```text
create raw_log(entry_type=ephemeris_event, source_type=imported_event, occurred=record.occurred, raw_text=record.text, project=record.project)
create evidence_item(strength=imported_activity_event)
import creates no fact; Stage 3 may extract only narrow source-supported facts
```

## §19.2 Knowledge-State Snapshot Contract

This source contract declares the closed JSON object below as the complete record the `atlas` importer accepts. It accepts one knowledge-state snapshot on Atlas's own scales, with its trail segments and source-owned evidence references; it does not accept a ready-made Exp2Res fact, claim, confidence, or ownership level.

```json
{
  "source": "atlas",
  "record_id": "atlas:snapshot:2026-07-14T20:00:00+02:00",
  "domain": "knowledge_state",
  "as_of": "2026-07-14T20:00:00+02:00",
  "occurred": {
    "start": "2026-07-01T00:00:00+02:00",
    "end": "2026-07-14T20:00:00+02:00",
    "precision": "date_range",
    "confidence": "high"
  },
  "text": "Atlas snapshot as of 2026-07-14T20:00:00+02:00. Summary: Studied provenance and verifier-gate design through an evidence-backed trail. Knowledge state: subject provenance; scale atlas_learning_stage; value studied. Trail: Verifier-gate design trail from 2026-07-01T00:00:00+02:00 to 2026-07-14T20:00:00+02:00 with date_range precision and high confidence. Evidence reference: atlas:evidence:exp2res-verifier-design.",
  "summary": "Studied provenance and verifier-gate design through an evidence-backed trail.",
  "knowledge_state": [
    {
      "subject": "provenance",
      "scale": "atlas_learning_stage",
      "value": "studied"
    }
  ],
  "trail_segments": [
    {
      "label": "Verifier-gate design trail",
      "occurred": {
        "start": "2026-07-01T00:00:00+02:00",
        "end": "2026-07-14T20:00:00+02:00",
        "precision": "date_range",
        "confidence": "high"
      }
    }
  ],
  "evidence_references": [
    {
      "reference": "atlas:evidence:exp2res-verifier-design"
    }
  ],
  "path": "snapshots/atlas-2026-07-14.txt",
  "content_digest": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
}
```

Field requirements:

- `source` must equal the literal `atlas`.
- `record_id` is this contract's §19.4 source identity: the adapter's stable, non-empty identifier for the snapshot.
- `domain` must equal `knowledge_state`.
- `as_of` is an offset-aware source snapshot time.
- `occurred` is the complete §11.1 placement of the experience represented by the snapshot.
- `text` and `summary` are non-empty source voice.
- `knowledge_state` is a non-empty list of closed `{subject, scale, value}` objects whose members are non-empty strings.
- `trail_segments` and `evidence_references` are required lists that may be empty.
- A trail segment is a closed `{label, occurred}` object with a non-empty label and complete §11.1 placement.
- An evidence reference is a closed `{reference}` object with a non-empty source-owned logical ID.

Temporal and scale constraints:

- Snapshot-wide and trail-segment `occurred` values must each have a finite §16.7 uncertainty upper bound, so `precision = "unknown"` is invalid in this contract.
- The snapshot-wide interval must contain every trail segment's interval.
- Compared as a UTC instant, `as_of` must be at or after the snapshot-wide upper bound, including the upper bound of a singleton precision.
- A violation of either temporal constraint above is invalid acquisition.
- A snapshot-wide `TemporalConfidence` weaker than a segment's is legal and conservatively governs extracted fact placement.
- A segment never elevates the governing `RawLog.occurred.confidence`.
- Atlas scale names and values remain opaque strings: they gain no Exp2Res enum or ordering, and §19.4's float prohibition applies.
- The selfos-side adapter alone maps Atlas's exact scales, trails, and reference schema into these fields.

Source-text fidelity:

- The adapter-supplied `text` is the authoritative complete source rendering of the same snapshot represented by the structured members and maps verbatim to `RawLog.raw_text`.
- Exp2Res never constructs `text` by serializing, normalizing, summarizing, or translating the record.
- Before persistence, the importer requires each of the following to occur byte-exactly in `text`:
  - `summary`.
  - Every `knowledge_state` subject, scale, and value.
  - Every trail label, non-null bound's exact accepted ISO input string, precision literal, and confidence literal.
  - Every evidence `reference`.
  - The exact accepted `as_of` input string.
- Thus the persisted source projection contains every accepted structured source value rather than retaining only its hash; a mismatch is invalid acquisition.
- `occurred` maps unchanged to `RawLog.occurred`.
- `as_of` remains snapshot provenance and never substitutes for experience placement.
- `RawLog.recorded_at` remains the independent service-assigned import time under §5.4.

Referenced snapshot document:

- `path` and `content_digest` are required nullable members: omission is invalid, `path = null` requires `content_digest = null`, and a non-null path may carry either a non-null §19.4 digest or `null`.
- The path identifies the single source snapshot document represented by the linked `EvidenceItem`, not one member of `evidence_references`.
- Those `evidence_references` are inert logical source IDs and never path or fetch authority.
- The path/digest pair follows §19.4 rule 6 and maps only to `EvidenceItem.path` and its named digest metadata.
- Required nullable members give omission and explicit absence one record shape before §19.4 hashing.

The `knowledge_state_snapshot` strength is high only within §9.4's stated knowledge-attribution scope.

Import behavior:

```text
create raw_log(entry_type=atlas_snapshot, source_type=imported_artifact, occurred=record.occurred, raw_text=record.text)
create one evidence_item(strength=knowledge_state_snapshot, summary=record.summary, path=record.path, metadata.content_digest when non-null)
derive facts and claims only through Stages 3, 4, and 6; import promotes none directly
```

## §19.3 GitHub Commit Contract

This source contract declares the closed JSON object below as the complete record the `github` importer accepts. Its §19.4 source identity is not a separate field: the importer derives it as the exact string `<repo>@<commit_sha>` from the validated values below.

```json
{
  "source": "github",
  "repo": "owner/repo",
  "commit_sha": "0123456789abcdef0123456789abcdef01234567",
  "message": "Add verifier-gate schema",
  "files": ["exp2res/pipeline/verify_bullets.py"],
  "url": "https://github.com/owner/repo/commit/0123456789abcdef0123456789abcdef01234567",
  "author": {
    "name": "Avery Example",
    "email": "avery@example.com",
    "login": "avery-example"
  },
  "committer": {
    "name": "Casey Example",
    "email": "casey@example.com",
    "login": "casey-example"
  },
  "authored_at": "2026-07-14T09:15:00-04:00",
  "committed_at": "2026-07-14T14:20:00+01:00",
  "owner_attribution": "unknown"
}
```

Repository and commit identity:

- `source` must equal the literal `github`.
- `repo` is the adapter-supplied `owner/name` repository identity.
- `commit_sha` must match `^[0-9a-f]{40}$`; an abbreviated or uppercase SHA, or one containing any non-hexadecimal character, is invalid at acquisition.
- The derived source identity is the exact, non-normalized concatenation `<repo>@<commit_sha>`; no adapter value may supply or override it.
- The §19.4 identity and duplicate rules apply without a GitHub-specific exception.

Identity objects and upstream times:

- `author` and `committer` are required closed identity objects whose only permitted members are the optional nullable `name`, `email`, and `login` strings supplied by the adapter.
- Their values are inert provenance under §11's boundary and text-hygiene policy, not locally verified identities.
- `authored_at` and `committed_at` are required offset-aware datetimes recorded by the upstream source.
- The importer maps `committed_at` to `RawLog.occurred` as `OccurredAt(start=committed_at, end=None, precision="exact_datetime", confidence="high")`: the upstream record supplies an exact commit instant rather than an inferred temporal placement, and §12 rule 3 preserves its supplied offset in storage.
- `authored_at` remains separate provenance and never replaces that OccurredAt anchor.
- `RawLog.recorded_at` remains the independent service-assigned import time under §5.4.
- Temporal confidence `high` states only confidence in that source-recorded placement and grants no stronger evidence, attribution, or ownership semantics.

Source-reported locators:

- Each `files` member is a source-reported repository filename, and `url` is a source-reported locator.
- Both remain inert provenance under §29.4: neither selects, opens, dereferences, or fetches content, and neither grants filesystem or network authority in V1.

Owner attribution:

- `owner_attribution` is typed by `OwnerAttribution` (§10).
- When omitted, validation materializes `unknown` before §19.4 canonical serialization and content hashing, so omission and an explicit `unknown` have one validated record.
- The field is an upstream-adapter or owner assertion that Exp2Res preserves but neither verifies nor infers from `author` or `committer` identity strings.
- Only `owner_attribution = "owner"` creates `EvidenceItem(strength="commit_or_pr")`; every other canonical value creates `EvidenceItem(strength="artifact_reference")`.
- This mapping establishes only the evidential scope in §9.4.
- This mapping never supplies an `OwnershipLevel` and never bypasses §16.4.

Import behavior:

```text
create raw_log(entry_type=github_commit, source_type=imported_artifact)
create evidence_item(strength from owner_attribution mapping above)
extract only narrow source-supported implementation facts
```

## §19.4 Record Identity and Import Semantics

1. **Record shape.** Every record supplied to the §14.5 `ephemeris`, `atlas`, or `github` importer is exactly one closed source object declared by that importer's source contract in §19.1–§19.3; a wrapped record, a record carrying undeclared fields, and a record shaped for another source are invalid:

   - All fields follow §11's strict validation, boundary, and hygiene policy.
   - Each source contract names the field or derivation carrying its non-empty source identity string.
   - A record's `source` discriminator must equal the invoked importer's source system; a mismatch is invalid.
   - No record declares a contract version and no shared version field exists: a source contract changes only by a dated §19 spec decision.
   - Record shape is solely an acquisition-boundary rule and never selects, implies, or substitutes for a §12.14 database migration.

2. **Identity and idempotency.** Import identity is the exact, non-normalized pair (source system, source identity).

   - While holding the §8.1 writer lock, the service compares that identity against retained imported `RawLog` rows and persists the source system, the source identity, and the rule 3 content hash on each created `RawLog` as the §11 named metadata keys `source_system`, `source_record_id`, and `content_hash`.
   - The three keys are reserved service mappings: a source `metadata` value containing one is invalid rather than overwritten.
   - The merged metadata object, including the three keys, must remain within §11's key-count and byte budgets.
   - `RawLog.external_ref` retains only its source-provenance role.
   - Re-importing the same identity with the same content hash is an idempotent duplicate no-op: it creates no `RawLog`, `EvidenceItem`, or other row and is reported as `duplicate`.
   - The same identity with a different content hash is a plain rejected record — there is no separate conflict class — and the retained raw and evidence rows are not mutated.
   - The same content hash under a different identity creates an independent record.
   - Corrected upstream content must therefore arrive under a new identity or be rejected.
   - Corrected upstream content never updates the original raw record in place, and the owner's §14.4 correction flow remains the only reinterpretation channel (§5.3).

3. **Content hash.** The importer computes the content hash itself: SHA-256 over the exact §11 canonical-serialization bytes of the validated record, encoded as exactly 64 lowercase hexadecimal characters.

   - No record supplies, declares, or overrides it; a content-hash field in a record is an undeclared field under rule 1.
   - Because §11 deliberately leaves float rendering unpinned, a float anywhere in an integration record, including pass-through source metadata, is invalid at acquisition rather than hashed implementation-dependently.

4. **Multi-record payloads.** A multi-record payload or file — including an ephemeris JSONL file and any future multi-record source — is processed record by record in file order under one §8.1 writer lock.

   - §11's total-object-per-payload limit is also the maximum payload-size bound; this section introduces no second numeric cap.
   - Each record is classified and persisted independently in its own transaction: a rejected record fails only itself and never withdraws, delays, or invalidates an accepted one.
   - For repeated identities inside one payload, the first occurrence participates normally; a later occurrence with the same content hash is a counted duplicate, and one with a different content hash is rejected.
   - The only retry unit is the same payload or file, and rerunning converges: already-persisted records report as `duplicate`, and an unchanged validation error fails again deterministically.
   - No partial-resume cursor or background continuation exists.

5. **Result reporting.** The §14.14 rule 5 command-discriminated result reports complete `accepted`, `duplicate`, and `rejected` counts and per-class record lists in input order.

   - Each established input record receives a one-based `record_number`.
   - `source_record_id` is `null` only when that record's source identity is itself missing or invalid.
   - `raw_log_id` is non-null only for an `accepted` record actually created by a committed transaction.
   - Counts equal their list lengths, and the three lists partition every established input record exactly once.
   - Thus no rolled-back candidate ID is reported as created.
   - A failure too early to establish input record boundaries has no complete primary result and uses `result = null` under §14.14.
   - Every completed classification carries the full typed result.

6. **Referenced artifacts.** A source contract may pair a local `path` or `file:` URI with an optional `content_digest`: SHA-256 over the referenced file's exact bytes, encoded as exactly 64 lowercase hexadecimal characters.

   - When supplied, the importer records it as the §11 named `content_digest` metadata key on the linked `EvidenceItem` for that locator.
   - It is inert, non-authorizing provenance except for deterministic integrity comparison: it never selects a file or grants a read.
   - At every explicitly authorized §29.4 dereference, including acquisition when a source contract reads the referenced content, a supplied digest is recomputed and compared before the bytes are used.
   - A missing file is always reported, and a supplied-digest mismatch is reported as changed content.
   - Either state fails a required read closed, and content is never silently substituted, treated as unchanged, or omitted as though valid.
   - The locator grants no fetch or refresh authority under §29.4.
   - Both locator and content remain untrusted data under §29.5.

---
