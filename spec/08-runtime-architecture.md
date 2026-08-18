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

1. A workspace permits multiple concurrent readers and exactly one business writer.
2. The writer authority is an exclusive OS advisory lock (`flock`/`fcntl`-style or the platform equivalent) held on `.exp2res/lock`.
3. Authority is the held OS lock, never the file's existence: process death releases it automatically, and a leftover file is inert.
4. V1 has no PID or existence lock, stale-lock heuristic, manual lock repair, or `fsck` pass.
5. SQLite transactions remain required, but they cannot serialize the managed `out/` filesystem work that §13 couples to database state transitions.
6. Every command that can persist business data or managed outputs is a writer: the §14.2–§14.5 capture, import, and correction forms; `jd add`; `jd delete`; `workspace purge`; `gaps answer`; `logs delete`; `extract`; `detections generate`; the §14.9 assessment generation, repair, verification, and export forms; the §14.10 bullet-pack generation, verification, and export forms; `recompute`; and `db migrate`.
7. Every `list` or `show` form, `db status`, and the §14.17 `view serve` form is read-only and takes no writer lock.
8. On an existing workspace, a writer first applies the §12.14 compatibility gate.
9. The writer then acquires the workspace writer lock and re-establishes compatibility while holding it, before beginning its business operation, opening any write transaction, or enumerating or removing any managed output.
10. While holding that lock and before its business operation, every writer also applies §13.14's abandoned-publication preamble. §13.14 rule 5 owns sibling disposition and the boundary that an unreconciled residual stops managed-output publication without blocking a database mutation, owner deletion, or workspace purge.
11. The command releases the lock only after all of its transactions have committed or rolled back and all coupled filesystem work and residual-path reporting have completed.
12. A fresh §14.1 initialization and every §12.14 migration must persist `PRAGMA journal_mode = WAL`.
13. Every writer connection uses `PRAGMA synchronous = FULL`.
14. Every write transaction starts with `BEGIN IMMEDIATE`, so write contention is discovered before the first business write and a reported success has a durable commit.
15. One bounded contention timeout applies both to writer-lock acquisition and to each connection's `PRAGMA busy_timeout`; its default is 5000 ms.
16. Every connection follows §12's per-connection `PRAGMA foreign_keys = ON` execution-and-verification rule, subject only to its §12.14 migration exception.
17. Every database connection also executes `PRAGMA secure_delete = ON` and verifies that it took effect before business I/O, so SQLite overwrites deleted cell content instead of leaving it in reusable database pages.
18. After their deletion transaction has committed, `logs delete` (§14.11), `jd delete` (§14.15), and `workspace purge` (§14.16) each complete their database-erasure work with `PRAGMA wal_checkpoint(TRUNCATE)`, regardless of a managed-path cleanup failure or raw-log rebuild failure.
19. Point deletion does not run `VACUUM`.
20. Workspace purge first checkpoints the committed deletion, then runs `VACUUM` outside any transaction to rebuild the live main database without free pages, then runs a final `PRAGMA wal_checkpoint(TRUNCATE)` so the rewrite leaves no live WAL frames.
21. If any required checkpoint cannot truncate because of a concurrent reader, or the purge `VACUUM` or final checkpoint cannot complete, the database deletion remains committed but the command reports `deletion_incomplete` with the affected database or sidecar path and never reports success.
22. An empty or SQLite-maintained WAL/SHM pathname may remain while another reader is connected; success requires absence of purged content, not unsafe unlinking of a sidecar in use.
23. §29.6 states the physical-erasure limits of these application-level measures.
24. Every read-only command performs its §12.14 compatibility read and all of its business reads inside one explicit read transaction.
25. Under WAL snapshot isolation, a read-only command sees one coherent committed snapshot and cannot mix rows from generations committed before and after its read boundary; historical-inspection reads follow the same rule.
26. A writer performs the business reads for each mutation or export inside the corresponding `BEGIN IMMEDIATE` transaction, so that transaction has the same coherent-snapshot property.
27. Read-only commands take no workspace writer lock and may run concurrently with a writer.
28. For `view serve`, each served request — not the serving process — is one such read-only boundary: it performs its own compatibility read and all of its business reads inside one read transaction, and the process holds no lock, transaction, or cached read between requests (§30 rule 6). So a long-running server never presents a snapshot older than the request that serves it, and between requests it holds nothing a writer could contend with.
29. A request in flight is an ordinary concurrent reader with no special standing: a destructive command checkpointing against it may still report `deletion_incomplete` under this subsection's rules, exactly as it may against any other read-only command.
30. If the workspace writer lock or SQLite remains contended beyond the bounded timeout, the command fails with the stable machine-readable diagnostic class `workspace_busy`, emitted on one line.
31. For `view serve`, that command-failure rule applies to startup work before bind.
32. After bind, `view serve` holds no writer lock, and a request whose SQLite wait reaches that same timeout receives §30's `workspace_busy`/503 outcome, releases its read transaction, and leaves the serving command running for later requests until owner interruption.
33. No public command contract exposes a Python or SQLite stack trace; §14.14 defines the binding exit-code and JSON-envelope details.
34. If a process dies while holding the writer lock, the OS releases the advisory lock and SQLite restores a consistent database by rolling back any in-flight transaction through WAL recovery.
35. Managed outputs may remain stale or residual; the next writer applies the §13 preamble, reconciles §13.14's deterministically named candidate and rollback siblings, and applies §13.13 rules 4–6 rather than trusting any directory as current.
36. No lock repair or `fsck` pass is required.
37. The writer lock establishes the identity of the database it covers, read beside the lock entry itself rather than through the workspace pathname a second time. §13.14 rule 9 binds every managed-output mutation to that identity for as long as the lock is held.

---
