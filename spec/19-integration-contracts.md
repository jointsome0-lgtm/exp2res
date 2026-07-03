## §19. Integration Contracts

## §19.1 Tick-like Event Contract

```json
{
  "source": "tick-like",
  "event_id": "tick_001",
  "occurred_at": "2026-07-03T10:00:00+02:00",
  "event_type": "daily_note",
  "project": "Exp2Res",
  "text": "Worked on verifier loop design.",
  "metadata": {}
}
```

Import behavior:

```text
create raw_log(entry_type=tick_like_event)
create evidence_item(strength=imported_activity_event)
do not create strong fact without extraction/review
```

## §19.2 Atlas Artifact Contract

```json
{
  "source": "atlas",
  "artifact_id": "artifact:exp2res-verifier-design",
  "concepts": ["provenance", "verifier-loop", "grounded-generation"],
  "summary": "Design note about verifying generated claims.",
  "path": "docs/verifier.md"
}
```

Import behavior:

```text
create raw_log(entry_type=atlas_artifact_ref)
create evidence_item(strength=artifact_reference)
extract facts only if artifact content/source supports them
```

## §19.3 GitHub Commit Contract

```json
{
  "source": "github",
  "repo": "owner/repo",
  "commit_sha": "abc123",
  "message": "Add verifier loop schema",
  "files": ["exp2res/pipeline/verify_bullets.py"],
  "url": "..."
}
```

Import behavior:

```text
create raw_log(entry_type=github_commit)
create evidence_item(strength=commit_or_pr)
extract narrow implementation facts
```

---

