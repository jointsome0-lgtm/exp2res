## §19. Integration Contracts

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
