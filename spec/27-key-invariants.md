## §27. Key Invariants

§27 is an invariant index, not a second normative home. When an invariant mirrors a rule in another section, the cited rule remains canonical. If a cited rule changes by explicit decision, §27 must be updated in the same commit or explicitly drop that mirror.

```text
Raw logs are append-only to automation and deletable by their owner (§5.3).
Corrections are linked new evidence, never silent edits; they invoke recomputation (§5.3, §13.13).
Every current fact has retained source logs.
Every current self-claim has current source facts or signals.
Every current self-claim belongs to exactly one current assessment snapshot (§12 rule 10, §13.6).
Every current resume bullet has current source facts and retained source logs.
Every fact source is a non-null EvidenceItem link; raw-log provenance is derived through that item (§12.4).
Every typed JSON or polymorphic reference resolves to its current target when written (§12 rule 10).
At most one derived generation per lineage/scope is current; superseded history is inspect-only (§11, §13.13).
Owner deletion purges all derived database generations, verifies managed-export removal, and reports residual paths as incomplete before rebuilding (§13.13).
Uncertainty is preserved.
Contradictions are preserved.
Resume is an export, not the master model.
```

Canonical verification invariants:

- Ownership inflation is governed by §16.4.
- Temporal precision inflation is governed by §16.7.
- Metric invention is governed by §16.5.
- Unsupported production / impact / scale claims are governed by §16.6.
- Diagnostic claims are governed by §16.10.

---
