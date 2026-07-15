## §20. Suggested Repository Structure

Placement principles:

```text
1. One package per architectural layer under exp2res/; the § that owns a
   flow owns its module names: pipeline/ holds one module per active §13 stage,
   integrations/ one per §19 source, exports/ one per §13.12 output.
2. tests/ holds one behavioral test per §21 eval and one per §27
   invariant.
3. examples/ mirrors what the contracts already define: raw logs
   (§14.2–14.3), import payloads (§19), job descriptions (§13.8),
   outputs (§13.12).
4. File names inside the packages, tests/, and examples/ are content,
   not structure: the spec does not predict them.
5. The spec lives at the repo root (SDD.md map + spec/ + DECISION-LOG.md);
   no docs/ mirror restating it. The runtime workspace (`.exp2res/` and
   `out/`) is initialized under §14.1; §13.12 owns export content and §13.14
   owns managed-output paths/publication. It is not part of the source tree.
```

Normative skeleton — the shape §20 commits to; deeper paths are content:

```text
exp2res/
  pyproject.toml
  LICENSE
  README.md
  CLAUDE.md
  AGENTS.md
  SDD.md
  spec/
  DECISION-LOG.md

  exp2res/
    domain/
    storage/
    pipeline/
    integrations/
    llm/
    services/
    exports/

  tests/
  examples/
```

README.md content is specified by §26. Docs are not pre-named: a document exists when its content does; decisions land in DECISION-LOG.md, not pre-named ADRs.

---
