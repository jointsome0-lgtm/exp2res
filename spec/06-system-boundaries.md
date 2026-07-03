## §6. System Boundaries

## §6.1 Relation to Tick-like

Tick-like is the operational surface of the day.

It can provide:

```text
daily notes
routine check-ins
activity events
focus sessions
manual notes
exported JSONL
```

Exp2Res can import Tick-like data as raw logs or weak signals.

But Tick-like events do not automatically become strong experience facts.

Example:

```text
Tick-like event:
  "Worked on Exp2Res verifier"

Exp2Res interpretation:
  raw_log candidate, weak evidence

Not automatically:
  "Designed a verifier architecture"
```

## §6.2 Relation to Atlas

Atlas is the knowledge-state atlas.

It can provide:

```text
concepts
directions
materials
trail segments
artifact refs
knowledge-state context
frontier context
```

Exp2Res can use Atlas to understand the conceptual context of experience.

But Atlas does not decide career/self claims.

Example:

```text
Atlas trail:
  REST API -> Idempotency

Exp2Res possible use:
  context for an experience fact

Not automatically:
  "Strong backend distributed systems skill"
```

## §6.3 Relation to GitHub

GitHub can provide strong artifact evidence:

```text
commits
pull requests
issues
README files
design docs
tests
source code
```

But code existence does not automatically imply impact, production use, leadership, or mastery.

## §6.4 Relation to Resume Export

Resume export is a projection.

It must be grounded in:

```text
raw logs
experience facts
self-assessment claims
artifact evidence
verification status
```

Resume output must not mutate the internal model.

---

