## §17. Self-Assessment Report Format

Default output:

```markdown
# Self-Assessment Snapshot

Generated: YYYY-MM-DD
Scope: <AssessmentSnapshot.scope; AssessmentScope is defined only in §10>
Scope target: <AssessmentSnapshot.scope_target when present>

## 1. Summary

## 2. Strongly Supported Facts

## 3. Recurring Signals

## 4. Current Strengths

## 5. Weakly Supported Strengths

## 6. Gaps

## 7. Contradictions

## 8. Risks / Failure Modes

## 9. Unknowns

## 10. Questions Worth Answering

## 11. Evidence Map

## 12. Counterevidence
```

The tone should be:

```text
clear
specific
non-flattering
non-punitive
evidence-aware
```

Every rendered `SelfClaim` keeps its §16.11 status visible. The Summary renders the snapshot's required `narrative_summary` claim, so it is governed by the same status gate. `partially_supported` and `inferred_but_acceptable` content must not appear under Strongly Supported Facts; `needs_clarification` renders as uncertainty or a question; and `contradicted` renders with its contradiction and counterevidence. A snapshot outside the §16.11 assessment-export allowlist does not export.

The Gaps section renders status-bearing snapshot claims with `dimension = "gap"`. The Unknowns section renders no free-form snapshot prose: for every ID in the snapshot's `gap_question_ids` — complete and unanswered as of synthesis — it presents the referenced `GapQuestion.target_*`, `reason`, and `priority` as missing-information context. A question answered after synthesis renders with an explicit answered-since-synthesis marker; its answer reaches the model only through extraction and the next generation. Questions Worth Answering renders only the still-unanswered rows' `question` values. Missing, duplicate, superseded, or writer-output-inconsistent IDs fail before rendering under §12 rule 10; a post-synthesis answer is visible state, never a rendering or export failure. These references present uncertainty; they do not receive a status, alter §16.11 aggregation, or become independent inputs to Stage 10. Any declarative conclusion must instead be a status-visible `SelfClaim`.

Recurring Signals may render signal-derived language only through a current `SelfClaim` referenced by the snapshot, with that claim's status and source mapping intact. The report must not dump `SelfSignal` rows as independently reviewed conclusions.

The Contradictions section renders every contradiction referenced by the selected current snapshot, including its title, description, and both typed source references. There is no resolved/dismissed filter or resolution note. Superseded contradictions appear only with historical snapshot inspection, never through current export.

The Counterevidence section renders every non-empty `SelfClaim.counterevidence` list for the snapshot, not only lists on `contradicted` claims. Each block identifies the claim, keeps its §16.11 status visible, and labels the entries as verifier-grounded contrary-evidence annotations. Counterevidence is not a separate claim, cannot improve a claim or snapshot status, and cannot guide resume generation as an independent prose channel.

All Exp2Res-authored report prose is generated voice under §16.12. A quoted Evidence Map segment retains source voice only when it carries a typed source ID and is verified byte-for-byte as the referenced persisted value or a contiguous substring; otherwise it is generated voice. The renderer must not merge a validated source segment with generated prose before voice validation.

---
