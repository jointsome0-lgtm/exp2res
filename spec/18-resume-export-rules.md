## §18. Resume Export Rules

Resume export remains useful, but secondary.

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
snapshot, branch, bullet, fact, or self-claim is superseded
any required provenance ID does not resolve to a current retained entity
bullet source_log_ids differs from the raw logs reached through source_fact_ids
no source fact reaches a direct fact_sources row, EvidenceItem, and retained RawLog
bullet status is unsupported/rejected
bullet contains unsupported ownership, metric, production, or employment framing
```

---
