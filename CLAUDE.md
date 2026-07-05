@AGENTS.md

<!-- Single source of truth is AGENTS.md (shared with Codex and other tools). Add Claude-only rules below this line only if they cannot live in AGENTS.md. -->

## Security reviews go to Codex

Claude-only rule — the reason is Fable-specific, and in AGENTS.md it would just tell Codex to delegate to itself. Ecosystem-wide; the full version lives in tick-like's CLAUDE.md.

Adversarial security / threat-model reviews — red-teaming a spec §, abuse-case analysis, attack-surface probing of any future code — are **delegated to Codex** (`codex:rescue` or the codex plugin), not run by Claude in the first person.

- Reason, so nobody "fixes" this later: Fable's dual-use safeguards are documented (anthropic.com, Fable 5 announcement) to fall back to Claude Opus 4.8 on cybersecurity framing — a first-person adversarial pass can silently switch models and drop the thread mid-task. Codex is unaffected and gives a genuinely independent adversarial view.
- Claude's role is the correctness half (§ consistency, invariants, plan alignment) and converging Codex's findings with its own.
- Routing rule, not a license to ignore security: a concern noticed in passing still gets surfaced plainly (issue / § edit) — the adversarial probing is what goes to Codex.
