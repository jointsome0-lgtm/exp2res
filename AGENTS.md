# exp2res — agent instructions

Implementation-stage project. The design reference is `SDD.md` (map) + `spec/` (body, one file per §). It documents what exp2res is; it does not gate when code may change. Code lands phase by phase under §22.

## Current implementation frontier

Phase status has one home: README's "§22 phase status" paragraph. Sequencing has one home: the open issues. Neither is restated here, so neither goes stale here.

[selfos#25](https://github.com/jointsome0-lgtm/selfos/issues/25) governs URL-only shell composition.

## Public data boundary

Treat this as a public engine repository in the [selfos topology](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/architecture.md): specification, docs, code, and invented demo fixtures only. The owner's canonical store is a [private SQLite workspace](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/instance.md) outside this public repository. Resolve it through an explicit flag, then `EXP2RES_WORKSPACE`, then `instances.exp2res` in `~/.config/selfos/config.toml`; a public checkout is never a data destination. Real workspace data never enters this checkout, tracked or untracked: no real records or excerpts in docs, fixtures, prompts, issues, or review output. Author demo fixtures as the [synthetic persona](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/persona.md), invent every example, and include the literal marker `Vera Example` in every fixture file. Exception: a fixture whose format or spec rule forbids the marker — an opaque-ID-only format, or §16.14 generated prose in which the persona's name may not appear — is instead named in the closed `MARKER_EXEMPT_PATHS` list in `scripts/check_public_hygiene.py` with an in-file justification, and its invented lineage must remain checkable another way (marker-carrying inputs, persona-derived entity IDs). Deletion guarantees are canonical in the [selfos deletion contract](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/deletion.md), never restated here; exp2res's managed-data lifecycle inventory lives in spec §29. Enable the committed pre-commit hook once per clone with `git config core.hooksPath .githooks`.

## How work happens

- Default: issue → PR. No spec edit unless the PR moves a documented contract.
- A PR that moves a documented contract updates the owning § file in the same PR.
- Trade-offs get one Decision Log line; the rationale lives in the issue or commit.
- § numbers are stable anchors: never renumber, never reuse a retired number.
- Findings and open questions go to GitHub issues, never to committed report files.
- Design before code only where reversal is expensive: schemas, deletion and
  lifecycle semantics, cross-repo contracts.

## Canon

Specification: `SDD.md` is the map (§ index and numbering rules); the body lives in `spec/` (one file per §, file names start with the § number); a § may additionally own an authored canon artifact beside its file in `spec/` (for example `spec/21-evals-cases.toml`) — normative spec text linked from the § file and named in its map line; decisions live in `DECISION-LOG.md`. The `@SDD.md` line below imports the map for consumers that expand it; every other consumer reads `SDD.md` directly. @-importing the body or the log is forbidden.
- point task → read the § file the map names, plus every authored canon artifact that file links;
- full read (all of `spec/` in index order, including authored canon artifacts) — only for full-pass reviews or cross-section decisions.

@SDD.md

## Skills

Shared skills install from the `selfos-skills` repo (an Agent Skills catalog): `npx skills add jointsome0-lgtm/selfos-skills --skill grill-sdd slice --agent claude-code --global --yes` (full catalog: `--skill '*'`). To grill the spec: `/grill-sdd`. If a needed skill is missing from a session, ask the user to install/update it with the same command.

Decision Log entries follow the vendored grammar
(`scripts/check_decision_log.py`, checker 1.1.0, baseline 2026-07-15 for
pre-adoption history) and are one or two sentences — the decision, then
the rejected alternative — linted at an 80-word ceiling with a 40-word
warning.

Git worktrees: create them only in `.worktrees/<name>` inside the repo (globally gitignored via `~/.config/git/ignore`), never as sibling directories. Any work that will open a PR branches and builds in such a worktree, never in the primary checkout — the primary checkout stays on a clean `main` so parallel sessions don't fight for its index. Trivial read-only work and single-file doc edits on a clean main need no worktree. Remove the worktree and delete its local branch once its PR merges.

## Context budget

Every domain must fit in one agent context window whole: ≤150k tokens
(bytes ÷ 4 is close enough). Data files never count.
The whole repo fitting is the goal; 3× reduction comes before any new
abstraction. Prefer deleting and merging over splitting into packages —
a domain that needs a map to be read is too big. When a change grows a
domain past the budget, the change is wrong, not the budget.

Budget counts everything an agent reads to work in a domain: its code,
its spec section, and its tests. Specs and tests shrink with the code —
a spec longer than the code it describes is a smell. Archives (exec
plans, review logs, decision-log history) are not read and do not count;
keep them clearly out of the reading path.

Baseline (2026-08-20): 366k code; largest domain `services` 82k. Every domain fits today.
Target: whole package ≤150k. Spec 168k — cut 3×
(`21-evals-cases.toml` 40k is data, not text); tests 413k follow the code.

## Style

- Avoid code comments unless explicitly asked to add comments.
- Deliver what was asked, at the scope asked — no extra features,
  refactoring, or abstractions beyond the task.
- In prose (PR text, docs, summaries): lead with the outcome, cut
  anything that doesn't change what the reader does next.
