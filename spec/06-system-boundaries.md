## §6. System Boundaries

## §6.1 Relation to Tick-like

Tick-like is the operational surface of the day.

The direct Exp2Res import boundary accepts only activity-domain evidence:

```text
diary and daily notes
verbal work notes
focus and time aggregates
```

The selfos-side adapter maps Tick-like exports into the source-agnostic activity record accepted by §19.1; Exp2Res does not accept or encode Tick-like's wire schema. A learning event is not imported directly as knowledge state: its knowledge aspect arrives only through the Atlas snapshot in §6.2/§19.2, while its time or activity aspect may be mapped into §19.1 as ordinary activity evidence. Each accepted activity record persists as a `RawLog` plus linked `EvidenceItem` and creates no `SelfSignal` directly.

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

The V1 Exp2Res importer accepts the §19.2 knowledge-state snapshot: knowledge state expressed on Atlas's own scales, trail segments, and evidence references. The selfos-side adapter owns the exact Atlas-to-snapshot mapping; Exp2Res neither embeds Atlas's internal schema nor interprets an Atlas scale as an Exp2Res confidence, ownership level, signal, or claim.

But Atlas does not decide career/self claims.

Example:

```text
Atlas knowledge-state snapshot:
  "Studied idempotent REST API design; trail and evidence references attached"

Exp2Res possible use:
  source-attributed support for a narrow learning-grade fact

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

## §6.4 Relation to the Verified Bullet-Pack Export

The verified bullet-pack export is a projection; a full resume document model is a named post-mirror iteration (§18).

It must be grounded in:

```text
raw logs
experience facts
self-assessment claims
artifact evidence
verification status
```

Bullet-pack output must not mutate the internal model.

---
