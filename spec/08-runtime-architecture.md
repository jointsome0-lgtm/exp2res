## §8. Runtime Architecture

V1 should be local-first and CLI-first.

Recommended stack:

```text
Python
Typer
SQLite
Pydantic
pytest
Markdown
JSON
LLM provider abstraction
```

The database compatibility and migration contract is specified in §12.14.

The system should be implementable without a web app.

All pipeline stages should be callable as testable service functions.

LLM use is allowed, but all LLM outputs must be structured, validated, and verified.

## §8.1 Workspace Concurrency and Locking

A workspace permits multiple concurrent readers and exactly one business writer. The writer authority is an exclusive OS advisory lock (`flock`/`fcntl`-style or the platform equivalent) held on `.exp2res/lock`. Authority is the held OS lock, never the file's existence: process death releases it automatically, and a leftover file is inert. V1 has no PID or existence lock, stale-lock heuristic, manual lock repair, or `fsck` pass. SQLite transactions remain required, but they cannot serialize the managed `out/` filesystem work that §13 couples to database state transitions.

Every command that can persist business data or managed outputs is a writer: the §14.2–§14.5 capture, import, and correction forms; `jd add`; `jd delete`; `workspace purge`; `gaps answer`; `logs delete`; `extract`; `detections generate`; `signals generate`; `assess generate`; `assess verify`; `export assessment`; `resume generate`; `verify --branch`; `export resume`; `recompute`; and `db migrate`. Every `list` or `show` form and `db status` is read-only and takes no writer lock. On an existing workspace, a writer first applies the §12.14 compatibility gate, then acquires the workspace writer lock and re-establishes compatibility while holding it before beginning its business operation, opening any write transaction, or enumerating or removing any managed output. Every writer transaction opens with `BEGIN IMMEDIATE`; the command releases the lock only after all of its transactions have committed or rolled back and all coupled filesystem work and residual-path reporting have completed.

A fresh §14.1 initialization and every §12.14 migration must persist `PRAGMA journal_mode = WAL`. Every writer connection uses `PRAGMA synchronous = FULL`, and every write transaction starts with `BEGIN IMMEDIATE`, so write contention is discovered before the first business write and a reported success has a durable commit. One bounded contention timeout applies both to writer-lock acquisition and to each connection's `PRAGMA busy_timeout`; its default is 5000 ms. Every connection follows §12's per-connection `PRAGMA foreign_keys = ON` execution-and-verification rule, subject only to its §12.14 migration exception.

Every database connection also executes `PRAGMA secure_delete = ON` and verifies that it took effect before business I/O, so SQLite overwrites deleted cell content instead of leaving it in reusable database pages. After their deletion transaction has committed, `logs delete` (§14.11), `jd delete` (§14.15), and `workspace purge` (§14.16) each complete their database-erasure work with `PRAGMA wal_checkpoint(TRUNCATE)`, regardless of a managed-path cleanup failure or raw-log rebuild failure. Point deletion does not run `VACUUM`. Workspace purge first checkpoints the committed deletion, then runs `VACUUM` outside any transaction to rebuild the live main database without free pages, then runs a final `PRAGMA wal_checkpoint(TRUNCATE)` so the rewrite leaves no live WAL frames. If any required checkpoint cannot truncate because of a concurrent reader, or the purge `VACUUM` or final checkpoint cannot complete, the database deletion remains committed but the command reports `deletion_incomplete` with the affected database or sidecar path and never reports success. An empty or SQLite-maintained WAL/SHM pathname may remain while another reader is connected; success requires absence of purged content, not unsafe unlinking of a sidecar in use. §29.6 states the physical-erasure limits of these application-level measures.

Every read-only command performs its §12.14 compatibility read and all of its business reads inside one explicit read transaction. Under WAL snapshot isolation, it sees one coherent committed snapshot and cannot mix rows from generations committed before and after its read boundary; historical-inspection reads follow the same rule. A writer performs the business reads for each mutation or export inside the corresponding `BEGIN IMMEDIATE` transaction, so that transaction has the same coherent-snapshot property. Read-only commands take no workspace writer lock and may run concurrently with a writer.

If the workspace writer lock or SQLite remains contended beyond the bounded timeout, the command fails with the stable machine-readable diagnostic class `workspace_busy`, emitted on one line. No public command contract exposes a Python or SQLite stack trace; §14.14 defines the binding exit-code and JSON-envelope details.

If a process dies while holding the writer lock, the OS releases the advisory lock and SQLite restores a consistent database by rolling back any in-flight transaction through WAL recovery. Managed outputs may remain stale or residual; the next operation applies the §13 preamble and §13.13 rules 4–6 rather than trusting them as current. No lock repair or `fsck` pass is required.

---
