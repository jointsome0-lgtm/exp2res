# exp2res — agent instructions

Design-stage project: no code yet; the primary artifact is the specification — `SDD.md` (map) + `spec/` (body, one file per §).
The current task is refining the SDD: sharpening requirements, finding contradictions and gaps, proposing alternatives. No code until the SDD is agreed.

## Data boundary

This is a public engine repository in the [selfos topology](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/architecture.md): specification, docs, and — once implementation lands — code and invented fixtures only. The owner's real data belongs in a private exp2res workspace (a SQLite database outside any public checkout) and never appears here in any form: no real records or excerpts in docs, fixtures, prompts, issues, or review output — examples are invented. Deletion guarantees are canonical in [selfos deletion](https://github.com/jointsome0-lgtm/selfos/blob/main/docs/deletion.md), never restated here; exp2res's managed-data lifecycle inventory lives in spec §29.

## SDD refinement rules

- Review findings → GitHub issues (section quote + severity). Never create report files.
- Review verdicts are falsifiable: BLOCKED | NEEDS_FIXES | APPROVED_WITH_NOTES. "Fine overall" is forbidden.
- A decision = an SDD edit in the same pass + a Decision Log line + the rationale in the commit message.
- A session after which the SDD did not change and no issue was opened or closed did not happen.

## Canon

Specification: `SDD.md` is the map (§ index and numbering rules); the body lives in `spec/` (one file per §, file names start with the § number); decisions live in `DECISION-LOG.md`. The map is imported into session context (line below); @-importing the body or the log is forbidden:
- point task → pick the § from the map's index and read only its file in `spec/`;
- full read (all of `spec/` in index order) — only for full-pass reviews or cross-section decisions.

@SDD.md

## Skills

Shared skills ship as the `sdd` plugin from the `selfos-skills` repo (a Claude Code plugin marketplace): `/plugin marketplace add jointsome0-lgtm/selfos-skills` (or the local checkout `~/projects/selfos-skills`), then `/plugin install sdd@selfos`. To grill the spec: `/sdd:grill-sdd`. If a needed skill is missing from a session, ask the user to install/update the plugin.
