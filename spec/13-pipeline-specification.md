## §13. Pipeline Specification

## §13.1 Stage 1 — Raw Capture

Inputs:

```text
manual daily logs
manual retrospective logs
gap answers
corrections
Tick-like JSONL exports
Atlas artifact refs
Atlas trail refs
GitHub commits / PRs / issues
local design docs
notes
```

Outputs:

```text
raw_logs
evidence_items
```

Rules:

1. Raw text must be non-empty.
2. recorded_at must always be set.
3. occurred precision must always be explicit.
4. Imported artifacts must keep source URI/path.
5. Nothing is interpreted as a strong fact at capture time.

## §13.2 Stage 2 — Evidence Normalization

Purpose:

```text
Convert raw logs and imported artifacts into referenceable evidence units.
```

Example:

```text
GitHub commit -> EvidenceItem(code_artifact)
Tick-like daily note -> EvidenceItem(imported_activity_event)
Atlas artifact ref -> EvidenceItem(artifact_reference)
Manual retro memory -> EvidenceItem(manual_claim)
```

Normalization does not create self-claims.

## §13.3 Stage 3 — Experience Fact Extraction

Command:

```bash
exp2res extract
```

Input:

```text
raw_logs + evidence_items
```

Output:

```text
experience_facts
fact_sources
```

Rules:

1. Extract atomic claims.
2. Preserve temporal precision.
3. Preserve ownership level.
4. Do not infer metrics unless evidence explicitly contains metrics.
5. Do not infer production use unless explicitly present.
6. Every fact must link to at least one raw log.
7. Fact confidence must reflect source strength.

Bad fact:

```text
The user is strong at agent systems.
```

Good facts:

```text
The user designed a verifier loop for Exp2Res.
The user wrote a system design document for Atlas.
The user repeatedly worked with provenance-heavy local-first system ideas.
```

## §13.4 Stage 4 — Gap and Contradiction Detection

Command:

```bash
exp2res gaps
exp2res contradictions
```

Gap triggers:

```text
missing_metric
missing_scale
missing_ownership
missing_context
ambiguous_time
ambiguous_claim
weak_evidence
unsupported_skill_claim
unclear_artifact_status
```

Contradiction examples:

```text
Fact A: user claims strong project execution.
Fact B: user repeatedly reports burnout under plans.

Fact A: resume bullet says production-grade.
Fact B: evidence only supports local prototype.
```

## §13.5 Stage 5 — Self-Signal Extraction

Command:

```bash
exp2res signals generate
```

Input:

```text
experience_facts
evidence_items
gap answers
contradictions
```

Output:

```text
self_signals
```

Signal categories:

```text
skill_signal
interest_signal
direction_signal
execution_pattern
avoidance_pattern
constraint_signal
capacity_signal
contradiction_signal
```

Example signal:

```json
{
  "signal_type": "direction_signal",
  "statement": "The user repeatedly returns to local-first provenance-heavy systems.",
  "supporting_fact_ids": ["fact_storyworm_001", "fact_exp2res_004", "fact_atlas_002"],
  "confidence": "medium"
}
```

## §13.6 Stage 6 — Self-Assessment Synthesis

Command:

```bash
exp2res assess generate
```

Input:

```text
self_signals
experience_facts
contradictions
gap questions
```

Output:

```text
self_claims
assessment_snapshots
```

Assessment dimensions:

```text
technical_skill
domain_interest
working_style
execution_capacity
constraint
risk
gap
trajectory
identity_hypothesis
```

The assessment must include:

```text
supported strengths
weakly supported strengths
recurring interests
known gaps
risks / failure modes
contradictions
unknowns
counterevidence
next questions
```

## §13.7 Stage 7 — Assessment Verification

Command:

```bash
exp2res assess verify --snapshot <id>
```

Verifier checks:

1. Every self-claim has sources.
2. Confidence matches evidence strength.
3. Counterevidence is not hidden.
4. Identity claims are not over-broad.
5. Self-assessment does not become motivational fiction.
6. No clinical/diagnostic claims are generated.
7. No resume-style overclaiming leaks into mirror mode.

## §13.8 Stage 8 — Job Description Parsing

Command:

```bash
exp2res jd add jobs/agent_engineer.md
```

Extract:

```text
required skills
preferred skills
responsibilities
seniority signals
domain signals
keywords
red flags
```

## §13.9 Stage 9 — Relevance Matching

Command:

```bash
exp2res match --jd <jd_id>
```

Purpose:

```text
Select relevant facts and self-claims for a specific external context.
```

The matcher must not invent relevance.
It can rank evidence by fit.

## §13.10 Stage 10 — Resume Generation

Command:

```bash
exp2res resume generate --jd <jd_id> --branch <name>
```

Hard constraints:

1. Use only supplied facts and supported self-claims.
2. Every bullet must include source_fact_ids.
3. Every bullet must include source_log_ids.
4. Do not invent metrics.
5. Do not upgrade ownership.
6. Do not upgrade temporal precision.
7. Do not turn learning into employment.
8. Do not turn independent projects into company roles.
9. Do not use unsupported production/scale claims.
10. Prefer concrete engineering language over self-description.

## §13.11 Stage 11 — Resume Verification

Command:

```bash
exp2res verify --branch <name>
```

Verifier inspects phrases, not only whole bullets.

Example:

```text
Bullet:
"Built a production-grade LLM evaluation platform that reduced hallucinations by 40%."

Verifier findings:
"Built" -> maybe supported
"production-grade" -> unsupported
"platform" -> partially supported
"reduced hallucinations by 40%" -> unsupported
```

## §13.12 Stage 12 — Export

Command:

```bash
exp2res export assessment --snapshot <id>
exp2res export resume --branch <name>
```

Assessment outputs:

```text
out/assessment/self_assessment.md
out/assessment/self_claims.json
out/assessment/evidence_map.json
out/assessment/gap_questions.md
out/assessment/contradictions.md
```

Resume outputs:

```text
out/<branch>/resume.md
out/<branch>/evidence_map.json
out/<branch>/verification_report.md
out/<branch>/gap_questions.md
```

Export must fail if required evidence links are missing.

---

