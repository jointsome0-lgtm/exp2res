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
requires review
hypothesis
```

A system that says “I don’t know” is more trustworthy than one that invents coherence.

## §5.3 Append-Only Experience Memory

All raw records are immutable.

If the user corrects a memory, Exp2Res stores a new correction event and recomputes downstream facts.

The old record is not deleted or silently edited.

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

Every promotion must be explicit, reviewed, and traceable.

---
