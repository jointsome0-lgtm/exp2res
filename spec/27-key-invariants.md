## §27. Key Invariants

§27 is an invariant index, not a second normative home. When an invariant mirrors a rule in another section, the cited rule remains canonical. If a cited rule changes by explicit decision, §27 must be updated in the same commit or explicitly drop that mirror.

```text
Raw logs are append-only to automation and deletable by their owner (§5.3).
Corrections are linked new evidence, never silent edits; they invoke recomputation (§5.3, §13.13).
Every current fact has retained source logs.
Every current self-claim has current source facts or signals.
Every current self-claim belongs to exactly one current assessment snapshot (§12 rule 10, §13.6).
Every current resume bullet has current source facts and retained source logs.
Every current resume branch names one exact current assessment snapshot, and each bullet lists exactly the supported member claims it used (§12 rule 10, §13.10, §18).
Every fact source is a non-null EvidenceItem link; raw-log provenance is derived through that item (§12.4).
Confidence never exceeds its §9.4 calibration ceiling or propagation cap, and evidence strength never authorizes content (§9.4, §16.4–§16.8).
Every typed JSON or polymorphic reference resolves to its current target when written (§12 rule 10).
Every matched JD requirement resolves in the exact typed ParsedJD supplied to Stage 10 (§12 rule 10, §13.10).
At most one derived generation per lineage/scope is current; superseded history is inspect-only (§11, §13.13).
Owner deletion purges all derived database generations, verifies managed-export removal, and reports residual paths as incomplete before rebuilding (§13.13).
Uncertainty is preserved.
Assessment unknowns are typed GapQuestion references, never free snapshot prose or independent resume inputs (§11.7, §13.6).
Contradictions are preserved as immutable members of Stage 4 replacement generations and cannot be transitioned in place (§5.9, §13.4).
Resume is an export, not the master model.
Status-bearing rows enter generation and export only through the §16.11 allowlists; unverified always blocks.
V1 review is verifier gating of assessment and resume projections, not an owner verdict on regenerated derived rows (§5.10).
Verification is one semantic pass; findings never invoke writers or mutate derived prose, and revisions require a replacement generation (§13.7, §13.11, §15.1).
Voice rules bind Exp2Res-authored language, never owner or system-of-record text; structural validation applies to both (§16.12).
The owner-controlled local workspace is the only canonical persistence domain; only a foreground user-initiated run may transmit exact §15.2–§15.9 typed inputs to the explicitly chosen provider, and no other LLM/network path exists (§29).
Secrets, ignored or non-selected files, ambient command/environment/filesystem content, and instruction-like source text never expand a prompt or authorize behavior (§29.4–§29.5).
```

Canonical verification invariants:

- Ownership inflation is governed by §16.4.
- Temporal precision inflation is governed by §16.7.
- Extracted `OccurredAt` provenance is governed by §13.3 and §15.2.
- Metric invention is governed by §16.5.
- Unsupported production / impact / scale claims are governed by §16.6.
- Diagnostic claims are governed by §16.10.
- Generated/source voice scoping is governed by §16.12.

---
