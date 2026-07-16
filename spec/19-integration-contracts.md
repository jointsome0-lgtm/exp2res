## §19. Integration Contracts

Every importer validates the payload's keys, types, closed-enum mappings, and required identifiers. Importer validation includes the boundary limits and text-hygiene rules in §11's Model validation policy. Its natural-language values remain system-of-record source voice under §16.12: §19.1 activity `text`, §19.2 snapshot `text` and `summary`, GitHub `message`, and local imported-document text are preserved and structure-only scanned at ingestion. A voice rule may constrain a later Exp2Res-authored fact, claim, report sentence, or resume bullet that uses this material, but may never reject, rewrite, or block the imported value itself because of its wording.

Imported source identifiers — the §19.4 envelope `source_record_id` and GitHub `commit_sha`/`repo` — remain provenance values in `RawLog.external_ref` or `RawLog.metadata` and must never become local entity `id` values. For GitHub, §19.3 defines how `repo` and `commit_sha` form the envelope's `source_record_id`, while §19.4 formalizes (`source_system`, `source_record_id`) as the stable import-idempotency identity; neither source value becomes a local entity ID. §19.4 owns the common import identity, duplicate, conflict, and batch semantics; each source subsection owns its integration body, and §19.3 owns any additional GitHub-specific acquisition rule.

Every local `path` or `file:` URI value carried by an import payload, including Atlas `path`, is governed by §29.4's POSIX-only acquisition and pre-serialization rules.

The JSON objects in §19.1–§19.3 are source-specific `body` shapes inside the one common §19.4 envelope; they are never accepted as unwrapped payload records.

## §19.1 Activity-Domain Evidence Contract

This source contract requires `source_system = "ephemeris"`, supports `contract_version = 1`, and declares the JSON object below as its closed §19.4 `body`. It is the source-agnostic activity-domain evidence intake Exp2Res accepts, not a Tick-like wire contract.

```json
{
  "source": "ephemeris",
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

All five fields are required; `source` must equal the envelope source system, `domain` must equal `activity`, `occurred` is a complete §11.1 value, `project` is a non-empty source project label, and `text` is non-empty source voice. The body is closed and has no pass-through metadata or knowledge-state field. Diary/daily notes, verbal work notes, and focus/time aggregates may enter only when the source explicitly reports activity; a plan or learning assertion does not establish completed activity merely by appearing in `text`. A learning record's structured knowledge-state, trail, or evidence-reference payload is invalid here; only a separately represented time/activity aspect may enter this contract, while knowledge state routes through §19.2. Text that mentions learning still carries only `imported_activity_event` scope and cannot establish §9.4 knowledge-state attribution on an Atlas scale.

The selfos-side adapter owns mapping Tick-like's events-replay records (`{timestamp, type, payload_version, payload}`) and calendar series into this body, including source-type interpretation and the §5.4 distinction between source recording time and described occurrence time. It maps the stable upstream identity to envelope `source_record_id`; a source timestamp that records only capture/replay time never populates `occurred`, while a timestamp whose upstream semantics place the described activity may contribute to `occurred`. Exp2Res assigns `RawLog.recorded_at` when the import enters the workspace, independently of body `occurred` and envelope `exported_at`; a source-only recording timestamp remains adapter-side provenance rather than being relabeled as either Exp2Res time field. No field or accepted value in this contract depends on Tick-like's upstream schema.

Import behavior:

```text
create raw_log(entry_type=ephemeris_event, source_type=imported_event, occurred=body.occurred, raw_text=body.text, project=body.project)
create evidence_item(strength=imported_activity_event)
import creates no fact; Stage 3 may extract only narrow source-supported facts
```

## §19.2 Knowledge-State Snapshot Contract

This source contract requires `source_system = "atlas"`, supports `contract_version = 1`, and declares the JSON object below as its closed §19.4 `body`. It accepts one knowledge-state snapshot on Atlas's own scales, with its trail segments and source-owned evidence references; it does not accept a ready-made Exp2Res fact, signal, claim, confidence, or ownership level.

```json
{
  "source": "atlas",
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

`source` must equal the envelope source system, `domain` must equal `knowledge_state`, `as_of` is an offset-aware source snapshot time, `occurred` is the complete §11.1 placement of the experience represented by the snapshot, and `text` and `summary` are non-empty source voice. `knowledge_state` is a non-empty list of closed `{subject, scale, value}` objects whose members are non-empty strings. `trail_segments` and `evidence_references` are required lists that may be empty; a trail segment is a closed `{label, occurred}` object with a non-empty label and complete §11.1 placement, and an evidence reference is a closed `{reference}` object with a non-empty source-owned logical ID. Snapshot-wide and trail-segment `occurred` values must each have a finite §16.7 uncertainty upper bound, so `precision = "unknown"` is invalid in this contract. The snapshot-wide interval must contain every trail segment's interval, and, compared as a UTC instant, `as_of` must be at or after the snapshot-wide upper bound, including the upper bound of a singleton precision; a violation is invalid acquisition. A snapshot-wide `TemporalConfidence` weaker than a segment's is legal and conservatively governs extracted fact placement; a segment never elevates the governing `RawLog.occurred.confidence`. Atlas scale names and values remain opaque strings: they gain no Exp2Res enum or ordering, and §19.4's float prohibition applies. The selfos-side adapter alone maps Atlas's exact scales, trails, and reference schema into these fields.

The adapter-supplied `text` is the authoritative complete source rendering of the same snapshot represented by the structured members and maps verbatim to `RawLog.raw_text`; Exp2Res never constructs it by serializing, normalizing, summarizing, or translating the body. Before persistence, the importer requires `summary`; every `knowledge_state` subject, scale, and value; every trail label, non-null bound's exact accepted ISO input string, precision literal, and confidence literal; every evidence `reference`; and the exact accepted `as_of` input string to occur byte-exactly in `text`. Thus the persisted source projection contains every accepted structured source value rather than retaining only its hash; a mismatch is invalid acquisition. `occurred` maps unchanged to `RawLog.occurred`, while `as_of` remains snapshot provenance and never substitutes for experience placement. `RawLog.recorded_at` remains the independent service-assigned import time under §5.4.

`path` and `content_digest` are required nullable members: omission is invalid, `path = null` requires `content_digest = null`, and a non-null path may carry either a non-null §19.4 digest or `null`. The path identifies the single source snapshot document represented by the linked `EvidenceItem`, not one member of `evidence_references`; those references are inert logical source IDs and never path or fetch authority. The path/digest pair follows §19.4 rule 6 and maps only to `EvidenceItem.path` and its named digest metadata. Required nullable members give omission and explicit absence one body shape before §19.4 hashing.

The `knowledge_state_snapshot` strength is high only within §9.4's stated knowledge-attribution scope.

Import behavior:

```text
create raw_log(entry_type=atlas_snapshot, source_type=imported_artifact, occurred=body.occurred, raw_text=body.text)
create one evidence_item(strength=knowledge_state_snapshot, summary=body.summary, path=body.path, metadata.content_digest when non-null)
derive facts, signals, and claims only through Stages 3–6; import promotes none directly
```

## §19.3 GitHub Commit Contract

This source contract requires `source_system = "github"`, supports `contract_version = 1`, and requires envelope `source_record_id` to equal the exact string `<repo>@<commit_sha>` formed from the validated body values below. The JSON object is the closed §19.4 `body`; its `source` discriminator must equal envelope `source_system` under §19.4 rule 1.

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

`repo` is the adapter-supplied `owner/name` repository identity. `commit_sha` must match `^[0-9a-f]{40}$`; an abbreviated or uppercase SHA, or one containing any non-hexadecimal character, is invalid at acquisition. The envelope `source_record_id` must match the exact, non-normalized concatenation required above or the record is invalid before §19.4 duplicate classification. The §19.4 identity, idempotency, and conflict rules apply without a GitHub-specific exception.

`author` and `committer` are required closed identity objects whose only permitted members are the optional nullable `name`, `email`, and `login` strings supplied by the adapter. Their values are inert provenance under §11's boundary and text-hygiene policy, not locally verified identities. `authored_at` and `committed_at` are required offset-aware datetimes recorded by the upstream source. The importer maps `committed_at` to `RawLog.occurred` as `OccurredAt(start=committed_at, end=None, precision="exact_datetime", confidence="high")`: the upstream record supplies an exact commit instant rather than an inferred temporal placement, and §12 rule 3 preserves its supplied offset in storage. `authored_at` remains separate provenance and never replaces that OccurredAt anchor; `RawLog.recorded_at` remains the independent service-assigned import time under §5.4. Temporal confidence `high` states only confidence in that source-recorded placement and grants no stronger evidence, attribution, or ownership semantics.

Each `files` member is a source-reported repository filename, and `url` is a source-reported locator. Both remain inert provenance under §29.4: neither selects, opens, dereferences, or fetches content, and neither grants filesystem or network authority in V1.

`owner_attribution` is typed by `OwnerAttribution` (§10). When omitted, validation materializes `unknown` before §19.4 canonical body serialization and content-hash verification, so omission and an explicit `unknown` have one validated body. The field is an upstream-adapter or owner assertion that Exp2Res preserves but neither verifies nor infers from `author` or `committer` identity strings. Only `owner_attribution = "owner"` creates `EvidenceItem(strength="commit_or_pr")`; every other canonical value creates `EvidenceItem(strength="artifact_reference")`. This mapping establishes only the evidential scope in §9.4 and never supplies an `OwnershipLevel` or bypasses §16.4.

Import behavior:

```text
create raw_log(entry_type=github_commit, source_type=imported_artifact)
create evidence_item(strength from owner_attribution mapping above)
extract only narrow source-supported implementation facts
```

## §19.4 Integration Envelope and Batch Semantics

1. **Envelope shape.** Every record supplied to the §14.5 `ephemeris`, `atlas`, or `github` importer is exactly one common envelope object with this closed typed shape; an unwrapped body or a body with undeclared fields is invalid:

   ```text
   IntegrationEnvelope {
     contract_version: int
     source_system: str
     source_record_id: str
     exported_at: datetime
     content_hash: str
     adapter_version: Optional[str] = None
     body: the closed shape selected from §19.1–§19.3 by source_system
   }
   ```

   All fields follow §11's strict validation, boundary, and hygiene policy. `source_system`, `source_record_id`, and a supplied `adapter_version` are non-empty structural strings; `source_record_id` is the adapter's stable identifier for the source record, and `exported_at` is the adapter/export timestamp and is offset-aware under §11. The selected §19 source contract fixes `source_system`, its supported `contract_version` values, and the exact `body` type. A body-level source discriminator, when that source contract declares one, must equal envelope `source_system`; a mismatch is invalid. External contract versions are integers beginning at 1. An unsupported future or retired version fails that record at acquisition and therefore fails its containing batch under rule 4; external payload versioning is solely an acquisition-boundary rule and never selects, implies, or substitutes for a §12.14 database migration.

2. **Identity and idempotency.** Import identity is the exact, non-normalized pair (`source_system`, `source_record_id`). While holding the §8.1 writer lock and inside the import transaction, the service compares that identity against retained imported `RawLog` rows and persists the envelope's `source_system`, `source_record_id`, and validated `content_hash` on each created `RawLog` as the §11 named metadata keys with those names. The three keys are reserved service mappings: a source `body.metadata` value containing one is invalid rather than overwritten, and the merged metadata object, including the three keys, must remain within §11's key-count and byte budgets. `RawLog.external_ref` retains only its source-provenance role.

   Re-importing the same identity with the same `content_hash` is an idempotent duplicate no-op: it creates no `RawLog`, `EvidenceItem`, or other row and is reported as `duplicate`. The same identity with a different hash is a fail-closed `conflict`: the retained raw and evidence rows are not mutated. The same hash under a different identity creates an independent record. Corrected upstream content must therefore arrive under a new identity or report a conflict; it never updates the original raw record in place, and the owner's §14.4 correction flow remains the only reinterpretation channel (§5.3).

3. **Content hash.** `content_hash` is SHA-256 over the exact §11 canonical-serialization bytes of `body`, encoded as exactly 64 lowercase hexadecimal characters. The importer recomputes and compares it before duplicate classification; a mismatch is an invalid record. Because §11 deliberately leaves float rendering unpinned, a float anywhere in an integration body, including pass-through source metadata, is invalid at acquisition rather than hashed implementation-dependently. Envelope fields outside `body`, including export time and adapter version, do not enter this hash.

4. **Batch semantics.** A multi-record payload or file — including an ephemeris JSONL file and any future batch source — is processed in file order within one §8.1 writer transaction. §11's total-object-per-payload limit is also the maximum batch-size bound; this section introduces no second numeric cap. An exact duplicate of a retained record is a counted no-op and does not fail the batch. For repeated identities inside the file, the first occurrence participates normally; a later occurrence with the same hash is a counted intra-batch duplicate, while a later occurrence with a different hash is a conflict. Any conflict or invalid record aborts the entire transaction, so no candidate `RawLog`, linked `EvidenceItem`, metadata, or other business row from that file persists.

   The only retry unit is the same payload or file. If an interruption occurred before commit, rerunning imports it normally; if commit completed before the caller lost the result, rerunning converges as duplicates. An unchanged validation error or conflict fails again deterministically; no partial-resume cursor, per-record commit, or background continuation exists.

5. **Result reporting.** The §14.14 rule 5 command-discriminated result reports complete `accepted`, `duplicate`, `conflict`, and `rejected` counts and per-class record lists in input order. Each established input record receives a one-based `record_number`; `source_record_id` is `null` only when that field itself is missing or invalid, and `raw_log_id` is non-null only for an `accepted` record actually created by the committed transaction. Counts equal their list lengths, and the four lists partition every established input record exactly once. On an aborted batch, `accepted` is empty and every otherwise insertable record is `rejected`; exact duplicates remain `duplicate`, conflicting records remain `conflict`, and invalid records are `rejected`. Thus no rolled-back candidate ID is reported as created. A failure too early to establish input record boundaries has no complete primary result and uses `result = null` under §14.14; every completed classification, including one that fails the batch, carries the full typed result.

6. **Referenced artifacts.** A source contract may pair a local `path` or `file:` URI with an optional `content_digest`: SHA-256 over the referenced file's exact bytes, encoded as exactly 64 lowercase hexadecimal characters. When supplied, the importer records it as the §11 named `content_digest` metadata key on the linked `EvidenceItem` for that locator. It is inert, non-authorizing provenance except for deterministic integrity comparison: it never selects a file or grants a read. At every explicitly authorized §29.4 dereference, including acquisition when a source contract reads the referenced content, a supplied digest is recomputed and compared before the bytes are used. A missing file is always reported, and a supplied-digest mismatch is reported as changed content; either state fails a required read closed, and content is never silently substituted, treated as unchanged, or omitted as though valid. The locator grants no fetch or refresh authority under §29.4, and both locator and content remain untrusted data under §29.5.

---
