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
bullet status is unsupported/rejected
bullet contains unsupported ownership, metric, production, or employment framing
```

---

