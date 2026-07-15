## §19. Integration Contracts

Every importer validates the payload's keys, types, closed-enum mappings, and required identifiers. Importer validation includes the boundary limits and text-hygiene rules in §11's Model validation policy. Its natural-language values remain system-of-record source voice under §16.12: Tick-like `text`, Atlas `summary` and referenced artifact text, GitHub `message`, and local imported-document text are preserved and structure-only scanned at ingestion. A voice rule may constrain a later Exp2Res-authored fact, claim, report sentence, or resume bullet that uses this material, but may never reject, rewrite, or block the imported value itself because of its wording.

Imported source identifiers — Tick-like `event_id`, Atlas `artifact_id`, and GitHub `commit_sha`/`repo` — remain provenance values in `RawLog.external_ref` or `RawLog.metadata` and must never become local entity `id` values. For GitHub, §19.3 defines how `repo` and `commit_sha` form the envelope's `source_record_id`, while §19.4 formalizes (`source_system`, `source_record_id`) as the stable import-idempotency identity; neither source value becomes a local entity ID. §19.4 owns the common import identity, duplicate, conflict, and batch semantics; each source subsection owns its source-specific body, and §19.3 owns any additional GitHub-specific acquisition rule.

Every local `path` or `file:` URI value carried by an import payload, including Atlas `path`, is governed by §29.4's POSIX-only acquisition and pre-serialization rules.

The JSON objects in §19.1–§19.3 are source-specific `body` shapes inside the one common §19.4 envelope; they are never accepted as unwrapped payload records.

## §19.1 Tick-like Event Contract

This source contract requires `source_system = "ephemeris"`, supports `contract_version = 1`, and declares the JSON object below as its closed §19.4 `body`.

```json
{
  "source": "ephemeris",
  "event_id": "tick_001",
  "occurred_at": "2026-07-03T10:00:00+02:00",
  "event_type": "daily_note",
  "project": "Exp2Res",
  "text": "Worked on verifier-gate design.",
  "metadata": {}
}
```

Import behavior:

```text
create raw_log(entry_type=ephemeris_event, source_type=imported_event)
create evidence_item(strength=imported_activity_event)
import creates no fact; Stage 3 may extract only narrow source-supported facts
```

## §19.2 Atlas Artifact Contract

This source contract requires `source_system = "atlas"`, supports `contract_version = 1`, and declares the JSON object below as its closed §19.4 `body`. The body may pair `path` with the optional `content_digest` defined in §19.4; when present, that field is part of this closed body shape.

```json
{
  "source": "atlas",
  "artifact_id": "artifact:exp2res-verifier-design",
  "concepts": ["provenance", "verifier-gate", "grounded-generation"],
  "summary": "Design note about verifying generated claims.",
  "path": "docs/verifier.md"
}
```

Import behavior:

```text
create raw_log(entry_type=atlas_artifact_ref, source_type=imported_artifact)
create evidence_item(strength=artifact_reference)
extract facts only if artifact content/source supports them
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
