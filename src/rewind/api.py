"""HTTP API for the Time-Travel Dashboard.

Thin JSON layer over the store: the tree graph, state/diff views, pending
effects, and the rewind action. Run locally with:

    uvicorn rewind.api:app --reload

Configuration via environment:
    REWIND_DATABASE_URL — store backend (default: local file rewind.db)
"""

from __future__ import annotations

import os
from typing import Any

from rewind import demo as demo_mod
from rewind.env import load_dotenv
from rewind.proxy import ToolProxy
from rewind.store import RewindStore

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'The HTTP API requires the api extra: pip install "rewind-agents[api]"'
    ) from exc


class CreateRunRequest(BaseModel):
    name: str


class RewindRequest(BaseModel):
    branch_id: str
    to_checkpoint_id: str
    new_branch_name: str | None = None


class CheckpointRequest(BaseModel):
    updates: dict[str, Any] | None = None
    deletes: list[str] = []
    label: str | None = None


class DemoStartRequest(BaseModel):
    interval: float = 2.0


def create_app(store: RewindStore | None = None) -> FastAPI:
    if store is None:
        load_dotenv()
        store = RewindStore.open(os.environ.get("REWIND_DATABASE_URL", "sqlite:///rewind.db"))
    proxy = ToolProxy(store)
    app = FastAPI(title="Rewind", description="Version control for AI agents")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/runs")
    def list_runs() -> list[dict[str, Any]]:
        return [r.__dict__ for r in store.list_runs()]

    @app.post("/api/runs")
    def create_run(req: CreateRunRequest) -> dict[str, Any]:
        return store.create_run(req.name).__dict__

    @app.get("/api/runs/{run_id}/tree")
    def get_tree(run_id: str) -> dict[str, Any]:
        try:
            # The dashboard's poll doubles as the demo clock (stateless,
            # so any process/Lambda instance can advance the storyboard).
            demo_mod.tick(store, run_id)
            return store.get_tree(run_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/checkpoints/{checkpoint_id}/state")
    def get_state(checkpoint_id: str) -> dict[str, Any]:
        try:
            return store.get_state(checkpoint_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/api/diff")
    def get_diff(a: str, b: str) -> dict[str, Any]:
        try:
            return {key: {"a": va, "b": vb} for key, (va, vb) in store.diff(a, b).items()}
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/branches/{branch_id}/checkpoints")
    def create_checkpoint(branch_id: str, req: CheckpointRequest) -> dict[str, Any]:
        try:
            ckpt = store.create_checkpoint(
                branch_id, updates=req.updates, deletes=req.deletes, label=req.label
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return ckpt.__dict__

    @app.get("/api/branches/{branch_id}/pending-effects")
    def pending_effects(branch_id: str) -> list[dict[str, Any]]:
        try:
            return [e.__dict__ for e in proxy.pending(branch_id)]
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/rewind")
    def rewind(req: RewindRequest) -> dict[str, Any]:
        try:
            result = store.rewind(req.branch_id, req.to_checkpoint_id, req.new_branch_name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "new_branch": result.new_branch.__dict__,
            "abandoned_branch": result.abandoned_branch.__dict__,
            "discarded_effects": [e.__dict__ for e in result.discarded_effects],
            "discarded_count": len(result.discarded_effects),
        }

    @app.post("/api/demo/start")
    def start_demo(req: DemoStartRequest) -> dict[str, Any]:
        return {"run_id": demo_mod.start(store)}

    @app.get("/api/demo/{run_id}/sent")
    def demo_sent(run_id: str) -> list[dict[str, Any]]:
        """Messages that actually left the sandbox (flushed after recovery)."""
        try:
            return demo_mod.sent_messages(store, run_id)
        except KeyError:
            return []

    return app


def app() -> FastAPI:  # uvicorn rewind.api:app --factory
    return create_app()
