## §19. Integration Contracts

Every importer validates the payload's keys, types, closed-enum mappings, and required identifiers. Importer validation includes the boundary limits and text-hygiene rules in §11's Model validation policy. Its natural-language values remain system-of-record source voice under §16.12: Tick-like `text`, Atlas `summary` and referenced artifact text, GitHub `message`, and local imported-document text are preserved and structure-only scanned at ingestion. A voice rule may constrain a later Exp2Res-authored fact, claim, report sentence, or resume bullet that uses this material, but may never reject, rewrite, or block the imported value itself because of its wording.

Imported source identifiers — Tick-like `event_id`, Atlas `artifact_id`, and GitHub `commit_sha`/`repo` — remain provenance values in `RawLog.external_ref` or `RawLog.metadata` and must never become local entity `id` values; duplicate-import and idempotency-key semantics are deferred to issues #33 and #52.

## §19.1 Tick-like Event Contract

```json
{
  "source": "tick-like",
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
create raw_log(entry_type=tick_like_event, source_type=imported_event)
create evidence_item(strength=imported_activity_event)
import creates no fact; Stage 3 may extract only narrow source-supported facts
```

## §19.2 Atlas Artifact Contract

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

```json
{
  "source": "github",
  "repo": "owner/repo",
  "commit_sha": "abc123",
  "message": "Add verifier-gate schema",
  "files": ["exp2res/pipeline/verify_bullets.py"],
  "url": "..."
}
```

Import behavior:

```text
create raw_log(entry_type=github_commit, source_type=imported_artifact)
create evidence_item(strength=commit_or_pr)
extract narrow implementation facts
```

---
