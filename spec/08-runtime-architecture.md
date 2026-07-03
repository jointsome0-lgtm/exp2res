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

The system should be implementable without a web app.

All pipeline stages should be callable as testable service functions.

LLM use is allowed, but all LLM outputs must be structured, validated, and verified.

---

