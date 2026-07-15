# Exp2Res — Experience to Self-Assessment to Verified Bullet Pack

> A mirror first. A verified-bullet-pack exporter second.

Exp2Res is a local-first, provenance-heavy self-assessment system.

It turns owner-controlled experience evidence that automation cannot rewrite into an honest model of skills, patterns, gaps, contradictions, and uncertainty. A verified bullet pack is a secondary export: job-targeted bullets are generated only from supported evidence and verified before export. A full resume document model is deferred to a post-mirror iteration.

## Project stage: design specification

**This repository contains no code yet.** It is the system design document (SDD) for a proposed system, developed spec-first: the design is being reviewed, red-teamed, and refined issue by issue before implementation starts. Treat everything here as a specification of intended behavior, not a description of shipped software.

## What is in this repository

- [`SDD.md`](SDD.md) — the map: a stable-numbered § index. Each section lives in its own file under [`spec/`](spec/) (`spec/NN-slug.md`).
- [`spec/`](spec/) — the specification body: domain model, typed contracts, SQLite schema, pipeline stages, CLI, LLM contracts, verification rules, evals, and acceptance criteria.
- [`DECISION-LOG.md`](DECISION-LOG.md) — dated one-line design decisions with rejected alternatives.

To read the design, start with the § index in `SDD.md` and open the section files you need; §1 (executive summary) and §28 (final design statement) are the shortest complete picture.

## Design boundaries

- **Public engine, private workspace.** This repository is a public engine in the [selfos topology](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/architecture.md): specification, docs, and — once implementation lands — code and invented fixtures only. The owner's canonical store is a [private SQLite workspace](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md) that lives outside this public repository — a public checkout is never a data destination. `EXP2RES_WORKSPACE` is reserved for locating it; the path discovery order is an explicit flag, then `EXP2RES_WORKSPACE`, then `instances.exp2res` in `~/.config/selfos/config.toml`. Real data and managed outputs never enter this repository. The deletion guarantee (logical deletion vs. purge, and what honestly remains) is canonical in the [selfos deletion contract](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/deletion.md) and is not restated here; exp2res's own managed-data lifecycle inventory is defined in [spec §29](spec/29-security-and-privacy.md).
- **Local-first and private by default.** The owner's workspace is the only canonical store; nothing is sent anywhere on system initiative. LLM calls happen only inside explicit, user-initiated pipeline runs against an explicitly selected provider.
- **Evidence over impressiveness.** Raw records are append-only to automation, every derived claim traces to evidence, contradictions are first-class, and verifier gates block flattery, inflated ownership, invented metrics, and unsupported generated-bullet claims.
- **The export never becomes the master model.** Experience model → self-assessment → export projection → verified bullet pack, in that order.

## Ecosystem

Exp2Res is the experience layer of [selfos](https://github.com/jointsome0-lgtm/selfos), a personal state platform, alongside [ephemeris](https://github.com/jointsome0-lgtm/ephemeris) (activity) and [atlas](https://github.com/jointsome0-lgtm/atlas) (knowledge).

## Security

There is no runnable code to report vulnerabilities against yet; the security and privacy design lives in [spec §29](spec/29-security-and-privacy.md). Until code lands, the [ecosystem-wide security policy](https://github.com/jointsome0-lgtm/selfos/blob/main/SECURITY.md) applies; report design-level security concerns as issues there or in this repository.

## Public hygiene

Before publishing or opening a pull request, run:

```bash
python3 scripts/check_public_hygiene.py
```

Every public repository must run the same local checker through both a pre-commit hook and a CI job. Both layers are required, not alternatives. Enable the committed pre-commit hook once per clone:

```bash
git config core.hooksPath .githooks
```

## License

[MIT](LICENSE)
