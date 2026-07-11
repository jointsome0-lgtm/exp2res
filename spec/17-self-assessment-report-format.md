## §17. Self-Assessment Report Format

Default output:

```markdown
# Self-Assessment Snapshot

Generated: YYYY-MM-DD
Scope: global / project / career / learning

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

Recurring Signals may render signal-derived language only through a current `SelfClaim` referenced by the snapshot, with that claim's status and source mapping intact. The report must not dump `SelfSignal` rows as independently reviewed conclusions.

The Contradictions section renders every contradiction referenced by the selected current snapshot, including its title, description, and both typed source references. There is no resolved/dismissed filter or resolution note. Superseded contradictions appear only with historical snapshot inspection, never through current export.

---
