## §14. CLI Specification

## §14.1 Initialize Project

```bash
exp2res init
```

Creates:

```text
.exp2res/
  exp2res.sqlite
  config.toml
logs/
evidence/
out/
```

## §14.2 Add Daily Log

```bash
exp2res log today
exp2res log today --project Exp2Res
exp2res log today --file notes/today.md
```

## §14.3 Add Retrospective Log

```bash
exp2res log retro
```

Interactive prompts:

```text
What period are we reconstructing?
How precise is this?
How confident are you?
Project/activity?
Describe what you remember.
```

## §14.4 Add Correction

```bash
exp2res correction add --log-id log_001
```

Stores a new raw log with `entry_type = correction`.

## §14.5 Import Evidence

```bash
exp2res import tick-like path/to/export.jsonl
exp2res import atlas path/to/atlas-export.json
exp2res import github --repo owner/name
exp2res import file docs/design.md --project Exp2Res
```

## §14.6 Extract Facts

```bash
exp2res extract
exp2res extract --log-id log_001
exp2res facts list
exp2res facts show fact_001
```

## §14.7 Generate Gaps and Contradictions

```bash
exp2res gaps
exp2res gaps answer gap_001
exp2res contradictions list
exp2res contradictions show contradiction_001
```

## §14.8 Generate Self-Signals

```bash
exp2res signals generate
exp2res signals list
```

## §14.9 Generate Self-Assessment

```bash
exp2res assess generate
exp2res assess generate --scope project --project Exp2Res
exp2res assess show snapshot_001
exp2res assess verify snapshot_001
exp2res export assessment --snapshot snapshot_001
```

## §14.10 Resume Export Flow

```bash
exp2res jd add jobs/agent_engineer.md
exp2res match --jd jd_001
exp2res resume generate --jd jd_001 --branch agent-engineer
exp2res verify --branch agent-engineer
exp2res export resume --branch agent-engineer
```

## §14.11 Inspect Raw Logs

```bash
exp2res logs list
```

---

