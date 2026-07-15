## §18. Verified Bullet-Pack Export Rules

The verified bullet pack remains useful, but secondary to the mirror. V1 exports selected, verified bullets plus the closed typed companions in §13.12; it does not claim a complete or styled resume document. A full resume document model is a named post-mirror iteration.

The canonical snapshot-anchor rule is: every `ResumeBranch` is created from exactly one current assessment snapshot named by §14.10's required `--snapshot` selector, persists that exact ID in its required `assessment_snapshot_id`, and has no implicit-latest or absent-anchor state. The selected snapshot must be eligible to anchor Stage 10 under §16.11 before any branch or bullet is inserted. Facts may still supply every concrete bullet, but the snapshot fixes the internal assessment generation context; its prose can guide generation only through a `supported` member claim listed on the bullet.

Pipeline:

```text
assessment snapshot
  + job description with typed ParsedJD requirements
  + facts and supported self-claims
  -> isolated relevance-aware bullet generation
     (selection and matching occur inside generation;
      relevance is persisted on each resume bullet)
  -> deterministic §13.10 planning and checks
  -> §13.11 verifier
  -> verified bullet-pack export
```

The snapshot, branch, bullets, facts, and self-claims in this pipeline must all be current (`superseded_at IS NULL`). Correction invalidates dependent branches, and owner deletion purges them (§13.13); neither path permits an old branch to survive as an apparently valid current bullet-pack export.

Minimum bullet contract:

```json
{
  "branch_id": "branch_001",
  "text": "...",
  "target_section": "selected_projects",
  "target_role_relevance": "high",
  "matched_jd_requirements": ["jdreq_001"],
  "source_fact_ids": ["fact_001"],
  "source_log_ids": ["log_001"],
  "source_self_claim_ids": [],
  "verification_status": "supported"
}
```

Bullet-pack export must fail if:

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
bullet matched_jd_requirements contains a duplicate, missing, free-form, or wrong-job requirement ID
no source fact reaches a direct fact_sources row, EvidenceItem, and retained RawLog
bullet status is outside the applicable §16.11 allowlist
bullet contains unsupported ownership, metric, production, or employment framing
```

Every Stage 10 branch persists its exact §14.10 job-description selection in `ResumeBranch.job_description_id`. Every `matched_jd_requirements` entry resolves through that branch association to a stable `JDRequirement.id` in the exact `ParsedJD` selected for Stage 10. A missing association fails verification/export; a display label may be rendered by dereferencing the requirement, but it may not replace the typed ID in stored or exported evidence maps.

`bullet_pack.md` renders every retained current branch bullet exactly once in §13.10 order. Its first logical line is exactly `# Verified Bullet Pack`, followed by one empty line; it then renders one `## ` heading for every §10 `ResumeTargetSection` member in declaration order, deriving heading text only by splitting the canonical snake-case value on `_`, capitalizing the first ASCII letter of each token, and joining the tokens with one space. A nonempty section has one empty line after its heading and then consecutive bullet lines, each exactly `- ` plus §17's escaped/continued bullet text; one empty line separates that section's final bullet from the next heading. An empty section contributes only its heading and, when another section follows, the one separator empty line. No empty logical line follows the final section, and §13.12 supplies the one final LF. Every heading renders even when its section is empty, and an empty section contains no filler. The only other renderer-authored text is §17's deterministic escaping/continuation syntax. Every factual sentence is inside the LF-newline- and NFC-normalized, escaped projection of one persisted `ResumeBullet.text`; the renderer adds no factual bridge, summary, transition, filler, or inferred coherence prose. The matching §13.12 `rendered_bullets` entry and complete typed evidence-map closure are mandatory for every bullet; that row grounds every sentence in its text through the same complete provenance sets. No second LLM pass may rewrite, order, deduplicate, or connect the bullets, and §15.6 input remains isolated to one bullet.

`ResumeBullet.text` and all system-authored export prose are generated voice under §16.12 and receive the full §16.2–§16.10 checks. An evidence-map excerpt remains source voice only with a typed source ID and byte-for-byte value/substring validation; otherwise it is generated voice. Source wording cannot cause the generated bullet-pack export to rewrite or reject the source record. §13.12 owns every companion field set and JSON byte rule; §17 owns the shared Markdown, empty-section, and repeated-render determinism rules, which apply unchanged here.

---
