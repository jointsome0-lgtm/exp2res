## §5. Core Principles

## §5.1 Truth Over Comfort

The system should prefer an uncomfortable accurate model over a comforting false one. For the normative phrase-level rules see §16.3 (anti-flattery) and §16.6 (production).

## §5.2 Uncertainty Is a Valid State

The system must not force closure.

Valid states include:

```text
unknown
unclear
weakly supported
contradicted
needs clarification
hypothesis
```

A system that says “I don’t know” is more trustworthy than one that invents coherence.

## §5.3 Owner-Controlled, System-Append-Only Experience Memory

Raw records are immutable to automation while retained: importers, extractors, verifiers, and every other system operation may append records but may never update or delete an existing `RawLog`.

The owner may hard-delete any raw record. This privacy override is not a contradiction of append-only operation: append-only protects the trail from the system, not from its owner. Owner deletion uses §14.11 and the privacy-first reset in §13.13; it must not be blocked by provenance foreign keys or by a failed rebuild.

If the user corrects a memory, Exp2Res stores a self-contained correction event linked to the target record and invokes the recomputation flow in §13.13 through §14.4. The target remains unchanged as a stored row, while the correction displaces its interpretation; §13.3 defines the lineage's effective records. An ordinary rerun supersedes the previous derived generation only when its replacement is valid; a source-changing correction invalidates stale current derivations even if rebuilding fails. Neither path silently edits derived payloads in place.

Correction history remains available, including superseded assessment snapshots. Owner deletion is stronger: it purges all derived database generations, removes every managed export it can, and reports any residual path as `deletion_incomplete` before rebuilding from the raw records that remain, because silently retaining derived copies could defeat deletion.

## §5.4 recorded_at Is Not occurred_at

Every raw record has two independent time dimensions:

```text
recorded_at = when the record was added to Exp2Res
occurred_at = when the described experience happened
```

This allows retrospective reconstruction without pretending exact memory.

## §5.5 Temporal Precision Must Not Be Inflated

If the user remembers:

```text
around spring 2026
```

the system must not later claim:

```text
April 12, 2026
```

unless stronger evidence exists.

## §5.6 Ownership Must Not Be Inflated

Ownership comparisons use the normative weak-to-strong `OwnershipLevel` order in §10. `unknown` is the weakest level and cannot authorize a stronger ownership claim.

The system may preserve or lower `ownership_level`; it must not raise it without stronger evidence. General claim `confidence` is a separate axis and cannot compensate for unsupported ownership.

## §5.7 Experience Is Not Resume

A real experience can be messy, partial, private, emotional, exploratory, or uncertain.

A resume is a constrained external representation of selected experience.

Therefore:

```text
Experience model > self-assessment > export projection > resume
```

The resume must never become the master model.

## §5.8 Self-Assessment Is Not Identity

Exp2Res can say:

```text
Current evidence suggests a pattern.
```

It should not say:

```text
This is who you are forever.
```

Self-assessment snapshots are time-bounded and revisable.

## §5.9 Contradictions Are First-Class

If evidence conflicts, the system stores the conflict.

It should not smooth contradictions away.

In V1 a contradiction is an immutable Stage 4 detection, not a user-resolvable workflow row. Outside the owner-deletion privacy reset, it remains in the complete current generation while current evidence conflicts; evidence-driven regeneration may omit it only after that conflict no longer exists, while the prior row becomes superseded history.

Example:

```text
Signal A: user repeatedly designs ambitious architectures.
Signal B: user reports burnout when trying to execute even minimal plans.
Assessment: high architecture drive, limited sustainable execution capacity under pressure.
```

## §5.10 No Automatic Semantic Promotion

Across systems and internal stages:

```text
check-in ≠ evidence of skill
artifact ≠ mastery
learning ≠ mastery
interest ≠ competence
plan ≠ experience
aspiration ≠ evidence
experience fact ≠ resume claim
Atlas artifact reference ≠ Exp2Res skill claim
Tick-like event ≠ self-assessment conclusion
```

Every promotion must be explicit and traceable. In V1, review means verification at the externally consumable projections: Stage 7 verifies assessment claims and derives the snapshot gate before assessment export or resume generation, and Stage 11 verifies resume bullets before resume export. Intermediate facts and signals do not carry an owner confirmation, dispute, or override state, and V1 has no owner-verdict workflow on regenerated claims or bullets.

---
