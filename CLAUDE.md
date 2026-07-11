@AGENTS.md

<!-- Single source of truth is AGENTS.md (shared with Codex and other tools). Add Claude-only rules below this line only if they cannot live in AGENTS.md. -->

## Security reviews go to Codex

Claude-only rule — the reason is Fable-specific, and in AGENTS.md it would just tell Codex to delegate to itself. Ecosystem-wide; the full version lives in tick-like's CLAUDE.md.

Adversarial security / threat-model reviews — red-teaming a spec §, abuse-case analysis, attack-surface probing of any future code — are **delegated to Codex** (`codex:rescue` or the codex plugin), not run by Claude in the first person.

- Reason, so nobody "fixes" this later: Fable's dual-use safeguards are documented (anthropic.com, Fable 5 announcement) to fall back to Claude Opus 4.8 on cybersecurity framing — a first-person adversarial pass can silently switch models and drop the thread mid-task. Codex is unaffected and gives a genuinely independent adversarial view.
- Claude's role is the correctness half (§ consistency, invariants, plan alignment) and converging Codex's findings with its own.
- Routing rule, not a license to ignore security: a concern noticed in passing still gets surfaced plainly (issue / § edit) — the adversarial probing is what goes to Codex.

## Picking the right models for workflows and subagents

Rankings, higher = better. Cost reflects what I actually pay (OpenAI has really
generous limits), not list price. Intelligence is how hard a problem you can
hand the model unsupervised. Taste covers UI/UX, code quality, API design, and
copy.

| model    | cost | intelligence | taste |
|----------|------|--------------|-------|
| gpt-5.6  | 9    | 8.9          | 7     |
| sonnet-5 | 5    | 5            | 7     |
| opus-4.8 | 4    | 7            | 8     |
| fable-5  | 2    | 9            | 9     |

How to apply:

- These are defaults, not limits. You have standing permission to override
  them: if a cheaper model's output doesn't meet the bar, rerun or redo the
  work with a smarter model without asking. Judge the output, not the price
  tag. Escalating costs less than shipping mediocre work.
- Cost is a tie-breaker only; when axes conflict for anything that ships,
  intelligence > taste > cost.
- The top two split by shape, not rank: fable-5 is stronger on architecture
  and interconnections; gpt-5.6 on driving a goal to completion and finding
  defects. Pick by task shape, not the raw intelligence number.
- Bulk/mechanical work (clear-spec implementation, data analysis, migrations):
  gpt-5.6 — it's effectively free.
- Anything user-facing (UI, copy, API design) needs taste ≥ 7.
- Reviews of plans/implementations: fable-5 or opus-4.8, optionally gpt-5.6 as
  an extra independent perspective.
- Never use Haiku.
- Mechanics: gpt-5.6 is only reachable through the Codex CLI — `codex exec` /
  `codex review` (my `~/.codex/config.toml` defaults to `gpt-5.6-sol` at xhigh
  effort). Use the codex plugin skills (`codex:rescue` for delegated
  diagnosis/fix passes, `codex:setup` for CLI health checks); for work they
  don't cover (investigation, data analysis, verification), run
  `codex exec -s read-only` directly with a self-contained prompt, and drop the
  sandbox flag only when codex must edit files.
- Claude models (sonnet-5, opus-4.8, fable-5) run via the Agent/Workflow model
  parameter.

Using gpt-5.6 inside workflows and subagents (the model parameter only takes
Claude models, so use a wrapper):

- Spawn a thin Claude wrapper agent with `model: 'sonnet', effort: 'low'` whose
  only job is to run `codex exec` via Bash and return the raw output verbatim.
- Write the full self-contained codex prompt yourself and pass it to the
  wrapper word-for-word. Wrappers never formulate or rewrite codex prompts —
  sonnet/opus don't write them well, and intermediary layers are banned — and
  you digest the result yourself.
- Treat codex claims (file:line, "tests are green", "done") as unverified until
  checked against artifacts. Codex is goal-driven and loves finding defects —
  excellent as a critic, so on long solo work call it at checkpoints
  (draft/diff → its findings → improve), not only at the end.
