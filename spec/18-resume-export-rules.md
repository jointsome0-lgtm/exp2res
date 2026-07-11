## §18. Resume Export Rules

Resume export remains useful, but secondary.

The canonical snapshot-anchor rule is: every `ResumeBranch` is created from exactly one current assessment snapshot named by §14.10's required `--snapshot` selector, persists that exact ID in its required `assessment_snapshot_id`, and has no implicit-latest or absent-anchor state. The selected snapshot must be eligible to anchor Stage 10 under §16.11 before any branch or bullet is inserted. Facts may still supply every concrete bullet, but the snapshot fixes the internal assessment generation context; its prose can guide generation only through a `supported` member claim listed on the bullet.

Pipeline:

```text
assessment snapshot
  + job description
  + facts and supported self-claims
  -> relevance-aware resume generation
     (selection and matching occur inside generation;
      relevance is persisted on each resume bullet)
  -> verifier
  -> export
```

The snapshot, branch, bullets, facts, and self-claims in this pipeline must all be current (`superseded_at IS NULL`). Correction invalidates dependent branches; owner deletion purges their database rows, attempts verified managed-export removal, and reports every residual path as incomplete. Neither path permits an old branch to survive as an apparently valid current export.

Minimum bullet contract:

```json
{
  "text": "...",
  "source_fact_ids": ["fact_001"],
  "source_log_ids": ["log_001"],
  "verification_status": "supported"
}
```

Export must fail if:

```text
bullet has no source_fact_ids
bullet has no source_log_ids
branch has no exact assessment snapshot anchor selected under §14.10
snapshot, branch, bullet, fact, or self-claim is superseded
snapshot status is outside the §16.11 Stage 10 anchor allowlist
any required provenance ID does not resolve to a current retained entity
bullet references a branch other than the exported branch
bullet source_self_claim_ids is not the exact set of supported claims used by the writer, or contains a claim outside the branch snapshot
bullet source_log_ids differs from the raw logs reached through source_fact_ids
no source fact reaches a direct fact_sources row, EvidenceItem, and retained RawLog
bullet status is outside the §16.11 resume-export allowlist
bullet contains unsupported ownership, metric, production, or employment framing
```

---
