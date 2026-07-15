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

The V1 importer accepts Tick-like JSONL events under §19.1 and persists each as a `RawLog` plus linked `EvidenceItem`. Tick-like imports do not create `SelfSignal` records directly.

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

Atlas can contain all of the data above, but the V1 Exp2Res importer accepts only artifact-reference payloads under §19.2. Concepts, directions, materials, trail segments, knowledge-state context, and frontier context remain outside the V1 import surface until they have explicit contracts and entry types.

But Atlas does not decide career/self claims.

Example:

```text
Atlas artifact reference:
  "Design note about an idempotent REST API"

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

The V1 GitHub importer accepts commit payloads under §19.3. Pull requests, issues, README files, design documents, tests, and source files may inform future integrations, but are not GitHub import payloads in V1; local design documents use §14.5 instead. Repository and commit selection, repository access, authentication, and remote fetching are upstream-adapter responsibilities; Exp2Res receives only the user-supplied local §19.3 envelope and performs no network selection or remote acquisition.

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

