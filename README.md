# Exp2Res — Experience to Self-Assessment to Resume

> A mirror first. A resume exporter second.

Exp2Res is a local-first, provenance-heavy self-assessment system.

It turns owner-controlled experience evidence that automation cannot rewrite into an honest model of skills, patterns, gaps, contradictions, and uncertainty. Resume generation is a secondary export: job-targeted bullets are generated only from supported evidence and verified before export.

## Project stage: design specification

**This repository contains no code yet.** It is the system design document (SDD) for a proposed system, developed spec-first: the design is being reviewed, red-teamed, and refined issue by issue before implementation starts. Treat everything here as a specification of intended behavior, not a description of shipped software.

## What is in this repository

- [`SDD.md`](SDD.md) — the map: a stable-numbered § index. Each section lives in its own file under [`spec/`](spec/) (`spec/NN-slug.md`).
- [`spec/`](spec/) — the specification body: domain model, typed contracts, SQLite schema, pipeline stages, CLI, LLM contracts, verification rules, evals, and acceptance criteria.
- [`DECISION-LOG.md`](DECISION-LOG.md) — dated one-line design decisions with rejected alternatives.

To read the design, start with the § index in `SDD.md` and open the section files you need; §1 (executive summary) and §28 (final design statement) are the shortest complete picture.

## Design boundaries

- **Local-first and private by default.** The owner's workspace is the only canonical store; nothing is sent anywhere on system initiative. LLM calls happen only inside explicit, user-initiated pipeline runs against an explicitly selected provider.
- **Evidence over impressiveness.** Raw records are append-only to automation, every derived claim traces to evidence, contradictions are first-class, and verifier gates block flattery, inflated ownership, invented metrics, and unsupported resume claims.
- **The resume never becomes the master model.** Experience model → self-assessment → export projection → resume, in that order.

## Ecosystem

Exp2Res is the experience layer of [selfos](https://github.com/jointsome0-lgtm/selfos), a personal state platform, alongside [ephemeris](https://github.com/jointsome0-lgtm/ephemeris) (activity) and [atlas](https://github.com/jointsome0-lgtm/atlas) (knowledge).

## Security

There is no runnable code to report vulnerabilities against yet; the security and privacy design lives in spec §29. Security policy defers to the selfos umbrella policy until code lands.

## License

[MIT](LICENSE)
