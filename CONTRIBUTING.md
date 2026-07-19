# Contributing to Rewind

Thanks for your interest! Rewind is young and moving fast — issues, ideas, and PRs are
all welcome.

## Development setup

Requirements: Python 3.11+.

```bash
git clone https://github.com/<you>/rewind
cd rewind
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

The test suite runs entirely against SQLite — no database server, Docker, or cloud
account required. Tests that exercise CockroachDB-specific features (vector indexing,
`AS OF SYSTEM TIME`) are skipped unless `REWIND_DATABASE_URL` points at a live cluster.

## Guidelines

- Keep the core SDK dependency-light. Anything heavy (LangGraph, FastAPI, psycopg)
  belongs in an optional extra.
- The versioned data model is **append-only** — no code path may destructively delete
  history. Rewinds create branches; they never erase.
- Add tests for new behavior. `pytest` must pass on SQLite.
- Run `ruff check .` and `ruff format .` before pushing.

## Commit style

Short imperative subject line, optional body explaining *why*. Example:

```
Add staged-effect discard on branch abandon

Staged effects on an abandoned branch must never flush; discarding them
at rewind time is what makes the side-effect story safe.
```

## Reporting issues

Open a GitHub issue with reproduction steps. For design discussions, open an issue
tagged `discussion` first — the data model is the load-bearing wall of this project.
