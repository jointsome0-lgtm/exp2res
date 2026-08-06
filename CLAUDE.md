# CLAUDE.md

Read [AGENTS.md](AGENTS.md) first: it is the shared agent contract for this
repository (SDD refinement rules, the spec canon, shared skills) and applies
to Claude Code in full.

Per the canon the SDD map is imported below:

- For a point task read only the § file you need in `spec/` plus any canon
  artifact it links.
- Never @-import the spec body or the log.
- Decisions live in [DECISION-LOG.md](DECISION-LOG.md).

@SDD.md

## Security reviews go to Codex

Claude-only rule — the reason is Fable-specific, and in AGENTS.md it would
just tell Codex to delegate to itself. The rule is ecosystem-wide; the full
version lives in ephemeris's CLAUDE.md.

Adversarial security / threat-model reviews are **delegated to Codex** (a
direct `codex exec` with a self-contained prompt), not run by Claude in the
first person. That covers red-teaming a spec §, abuse-case analysis, and
attack-surface probing of any future code.

- Reason, so nobody "fixes" this later: Fable's dual-use safeguards are
  documented (anthropic.com, Fable 5 announcement) to fall back to Claude
  Opus 4.8 on cybersecurity framing. A first-person adversarial pass can
  silently switch models and drop the thread mid-task. Codex is unaffected
  and gives a genuinely independent adversarial view.
- Claude's role is the correctness half (§ consistency, invariants, plan
  alignment) and converging Codex's findings with its own.
- Routing rule, not a license to ignore security: a concern noticed in
  passing still gets surfaced plainly (issue / § edit). The adversarial
  probing is what goes to Codex.

## Picking the right models for workflows and subagents

Rankings on a 0–10 scale, higher = better. Cost reflects what I actually pay
(OpenAI has really generous limits), not list price. Intelligence is how hard a
problem you can hand the model unsupervised. Taste covers UI/UX, code quality,
API design, and copy.

| model       | cost | intelligence | taste |
|-------------|------|--------------|-------|
| gpt-5.6     | 9    | 8.9          | 7     |
| sol-pro-web | 9    | 9            | 7     |
| opus-5      | 6    | 8.5          | 8.5   |
| fable-5     | 2    | 9            | 9     |

How to apply:

- These are defaults, not limits. You have standing permission to override
  them: if a cheaper model's output doesn't meet the bar, rerun or redo the
  work with a smarter model without asking. Judge the output, not the price
  tag. Escalating costs less than shipping mediocre work.
- Cost is a tie-breaker only.
- When axes conflict for anything that ships: intelligence > taste > cost.
- The top two split by shape, not rank — pick by task shape, not the raw
  intelligence number:
  - fable-5 is stronger on architecture and interconnections.
  - gpt-5.6 is stronger on driving a goal to completion and finding defects.
- Bulk/mechanical work (clear-spec implementation, data analysis, migrations):
  gpt-5.6 — it's effectively free.
- Anything user-facing (UI, copy, API design) needs taste ≥ 7.
- Reviews of plans/implementations: fable-5 or opus-5, optionally gpt-5.6 as
  an extra independent perspective.
- Fallback when Fable limits run out: fable-5 → opus-5 → gpt-5.6.
- Never use Haiku.
- When gpt-5.6 held the pen, independent review of that work goes to a Claude
  session (fable-5, otherwise opus-5). Degrading never weakens review
  independence.
- Mechanics: gpt-5.6 is only reachable through the Codex CLI — `codex exec` /
  `codex review` (my `~/.codex/config.toml` defaults to `gpt-5.6-sol` at xhigh
  effort).
  - Always run `codex exec` directly via Bash with a self-contained prompt you
    wrote.
  - `-s read-only` for pure reading/analysis.
  - `-s workspace-write` when it must edit files OR run tests/builds — test
    runs write caches and temp state, so read-only makes them fail or stall
    (this produced a false "verify.py hangs" finding once).
  - Health check: `codex --version` plus a trivial exec.
- Effort sizing (2026-07-19): the xhigh config default is for full
  adversarial/design passes only — open-ended search where a missed defect
  costs more than the hours.
  - Scoped real work — implementing from a clear spec, diagnosing a named bug,
    reviewing a medium diff, prep/measurement tasks — gets
    `-c model_reasoning_effort=high`.
  - Routine bounded checks — verifying a small diff, fidelity/gate checks,
    health checks — get medium.
  - Trivial/relay work gets low.
  - atlas 2026-07-19: xhigh on a 42-line verify diff burned ~10x wall-time for
    no extra findings.
- Parallel codex execs are fragile (atlas 2026-07-16: an exec hung ~35 min
  behind parallel sessions) — prefer one lighter run over a fan-out.
  Whole-diff consistency doesn't decompose per-finding.
- Claude models (opus-5, fable-5) run via the Agent/Workflow model
  parameter.
- Treat codex claims (file:line, "tests are green", "done") as unverified until
  checked against artifacts.
- Codex is goal-driven and loves finding defects — excellent as a critic, so on
  long solo work call it at checkpoints (draft/diff → its findings → improve),
  not only at the end.
