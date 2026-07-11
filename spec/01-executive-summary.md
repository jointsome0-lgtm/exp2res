## §1. Executive Summary

Exp2Res is a local-first system for converting lived experience into an honest, inspectable model of the user.

It stores raw logs, imported artifacts, notes, corrections, and answers as immutable records. From these records it extracts experience facts and then synthesizes a self-assessment model: skills, patterns, interests, constraints, recurring directions, evidence strength, gaps, contradictions, and uncertainty.

The system is designed around one central principle:

```text
The system must be honest before it is comforting.
```

A sweet false story may feel good, but if one detail changes — market feedback, a failed interview, a health constraint, a project collapse, a deadline, a contradiction — the false story can turn into a nightmare. Exp2Res should therefore preserve uncertainty, weakness, gaps, and counterevidence instead of smoothing them into a flattering narrative.

The resume pipeline remains in the system, but it is downstream:

```text
Self-assessment core
  -> relevance-aware resume generation for a job description
  -> verifier loop
  -> export
```

Every external claim must remain traceable to internal evidence.

---

