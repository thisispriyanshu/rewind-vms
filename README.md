# Rewind

[![CI](https://github.com/thisispriyanshu/rewind-vms/actions/workflows/ci.yml/badge.svg)](https://github.com/thisispriyanshu/rewind-vms/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Version control and a staging environment for AI agents.**

Deploying AI agents today is like letting a developer push straight to production with
no Git, no local server, and no code review. An agent forms a wrong belief at step 3,
and by step 40 every decision is built on that poisoned foundation — with no way to see
*when* it broke or to recover from it.

Rewind turns agentic memory from a write-only black box into a **versioned, rewindable,
reviewable system of record**:

- **Checkpoint** — auto-snapshot agent state after every step and tool call.
- **Branch** — fork agent state, inject a fix, and replay to test recovery.
- **Rewind** — roll the agent back to any point in time when it goes wrong.
- **Stage** — external side effects (Slack messages, API calls) are staged and
  idempotency-guarded, so a rewind never fires a duplicate or orphans a side effect.

Backed by [CockroachDB](https://www.cockroachlabs.com/) in production
(`AS OF SYSTEM TIME` time-travel reads, serializable transactions, distributed vector
indexing), with a zero-setup SQLite backend for local development.

> 🏗️ **Status: early development.** Built for the CockroachDB × AWS
> *"Build with Agentic Memory"* hackathon, designed to outlive it.

## How it works

```
                 ┌────────────────────────────────────────────┐
                 │        Agent Time-Travel Dashboard          │
                 │  (React: tree graph · diff view · Undo)     │
                 └───────────────┬────────────────────────────┘
                                 │  checkpoint/branch/rewind API
                 ┌───────────────▼────────────────────────────┐
   plug-in →     │            REWIND INTERCEPTOR               │
 LangGraph /     │  • Checkpointer (hooks per step/tool call)  │
 AG2 /           │  • Versioned State Store + VFS              │
 Claude Agent SDK│  • Idempotent Tool Proxy (stage/idem/dry)   │
 (via Bedrock)   │  • Vector Memory manager                    │
                 └───────┬───────────────────────┬─────────────┘
                         │                       │
                 ┌───────▼─────────┐      ┌──────▼───────────┐
                 │   CockroachDB   │      │   Amazon S3      │
                 │ • versioned KV  │      │ • VFS blobs      │
                 │ • VFS metadata  │      │ • artifacts      │
                 │ • vector index  │      └──────────────────┘
                 │ • staged effects│
                 └─────────────────┘
```

Everything the agent can mutate lives in the database as **append-only versioned rows** —
its working state, its files (via a virtual file system), and its semantic memory.
Rewinding never deletes anything: it forks a new branch from a clean checkpoint, and the
poisoned branch stays in history for audit and review.

External side effects can't be rolled back by a database — so they never happen directly.
They go through the **Idempotent Tool Proxy**:

| Mode | Behavior |
|---|---|
| `staged` | Effect is written as a pending row and only flushed to the real world when its checkpoint commits. On rewind, unflushed effects are discarded. |
| `idempotent` | Effect fires immediately but carries a stable idempotency key, so replay after a rewind is safe — no double-charge, no duplicate ticket. |
| `dry_run` | Effect runs against a mock and never leaves the sandbox. |

## Quickstart (local, no infrastructure)

```bash
pip install -e ".[dev]"
pytest
```

The SDK runs against SQLite out of the box. Try the full hero flow offline:

```bash
python examples/incident_demo.py
```

## Time-Travel Dashboard

The dashboard renders the checkpoint tree live, diffs any checkpoint against the
current head, and drives the rewind — including the "N pending Slack messages
discarded" moment. Two terminals:

```bash
# API (terminal 1)
pip install -e ".[api]"
uvicorn --factory rewind.api:app --port 8000

# Dashboard (terminal 2)
cd dashboard && npm install && npm run dev   # http://localhost:5173
```

Click **Start incident demo**: a scripted incident-response agent checkpoints
every step, poisons its own memory at step 3, and fails at step 6. Select the
last clean checkpoint, hit **⏪ Rewind & branch here**, and watch the agent
resume and resolve on the fresh branch.

## Connecting CockroachDB

Production features (`AS OF SYSTEM TIME` time travel, distributed vector
indexing, serializable checkpoint commits) come from CockroachDB. Copy
`.env.example` to `.env` and set:

```
REWIND_DATABASE_URL=postgresql://USER:PASSWORD@HOST:26257/defaultdb?sslmode=verify-full
```

For CockroachDB Cloud, install the cluster CA certificate first (from the
console's *Connect* dialog):

```bash
curl --create-dirs -o "$HOME/.postgresql/root.crt" \
  "https://cockroachlabs.cloud/clusters/<CLUSTER-ID>/cert"
# Windows: save to %APPDATA%\postgresql\root.crt
```

With `REWIND_DATABASE_URL` set, `pytest` additionally runs the live-cluster
integration tests (hero flow, idempotent replay, `AS OF SYSTEM TIME` reads).

## Project layout

```
src/rewind/    Python SDK — store, VFS, tool proxy, memory, API, demo agent
tests/         Test suite (runs on SQLite, no services needed)
examples/      Runnable walkthroughs of the hero flow
dashboard/     React Time-Travel Dashboard
```

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
