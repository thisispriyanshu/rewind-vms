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

> 🏗️ **Status: early development.** APIs may change while the core settles.

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

## Architecture

```mermaid
flowchart TB
    subgraph UI["Agent Time-Travel Dashboard (React)"]
        TREE["Tree graph · diff view · Rewind button · discard toast"]
    end
    subgraph INT["Rewind Interceptor (Python SDK)"]
        CKPT["Checkpointer\n(LangGraph RewindSaver)"]
        STORE["Versioned State Store + VFS"]
        PROXY["Idempotent Tool Proxy\nstaged / idempotent / dry-run"]
        MEM["Vector Memory manager"]
    end
    AGENT["LangGraph agent\n(LLM via Amazon Bedrock)"] --> CKPT
    UI -->|checkpoint / branch / rewind API| INT
    MCP["Claude Code / Cursor\nvia managed MCP server"] -.->|read-only, audited| CRDB
    CKPT --> STORE
    STORE --> CRDB[("CockroachDB\nversioned KV · VFS metadata\nVECTOR index · staged effects\nAS OF SYSTEM TIME")]
    PROXY --> CRDB
    MEM --> CRDB
    MEM -->|Titan embeddings| BR["Amazon Bedrock"]
    STORE -.->|large blobs| S3[("Amazon S3")]
```

Database features doing the heavy lifting:

- **`AS OF SYSTEM TIME`** (CockroachDB) — instant historical reads power the
  "state as it was before the bad write" view.
- **Serializable transactions** — each checkpoint commit moves state deltas,
  VFS writes, memory, and staged-effect metadata atomically.
- **Distributed vector indexing** — semantic recall runs inside the database
  (`embedding <=> query`), scoped to the checkpoint lineage, so rewinding
  also rewinds what the agent can remember.
- **Append-only versioned model** — rewind forks a branch; nothing is ever
  deleted, so the audit trail and tree view always have full history.

## Deployment

One command packages the API, dashboard, and dependencies into an AWS Lambda
with a public function URL (CockroachDB holds all state):

```bash
cd dashboard && npm run build && cd ..
python deploy/deploy.py
```

## Submission checklist (hackathon)

To make a competitive submission for the CockroachDB × AWS Hackathon, include the
following in your repository and demo assets:

- Public repo with an open-source license (included: MIT).
- A working demo URL (see `deploy/deploy.py` for Lambda deployment).
- A short video (<= 3 minutes) demonstrating the CockroachDB memory layer:
  - show vector recall, a poisoned step, then a rewind & recovery.
- Identify which CockroachDB tools you used and how:
  - Distributed Vector Indexing: implemented in `src/rewind/memory.py` and
    exercised by the integration tests.
  - (Optional helper) MCP: helper script `src/rewind/mcp.py` accepts a
    CockroachDB MCP connection snippet and writes `REWIND_DATABASE_URL` to `.env`.
- Identify which AWS services you used and how:
  - Amazon Bedrock: model + embedder adapters in `src/rewind/bedrock.py`.
  - Amazon S3: VFS blob storage support (S3 read/write helpers) in `src/rewind/vfs.py`.
  - AWS Lambda: `deploy/deploy.py` packages and deploys the API + dashboard.

Notes:
- The project runs locally with SQLite for quick demoing (`REWIND_DATABASE_URL=sqlite:///rewind.db`).
- For a live CockroachDB demo, set `REWIND_DATABASE_URL` to your cluster's URL
  (see CockroachDB Cloud Connect → MCP snippet), or use `src/rewind/mcp.py` to
  persist the connection into `.env` for `uvicorn` and `pytest` to pick up.


## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
