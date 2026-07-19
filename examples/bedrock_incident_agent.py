"""A real LLM-driven incident-response agent on Rewind + Bedrock + LangGraph.

Unlike examples/incident_demo.py (scripted), this agent *reasons*: Claude
decides each next action from the incident state and its semantic memory
(Titan embeddings in CockroachDB's vector index). Every step is a Rewind
checkpoint via the LangGraph RewindSaver, Slack messages are staged through
the Idempotent Tool Proxy, and the whole run is rewindable from the dashboard.

Requires: AWS credentials with Bedrock access in .env, and optionally
REWIND_DATABASE_URL for CockroachDB (defaults to local SQLite).

Run:  python examples/bedrock_incident_agent.py
"""

from __future__ import annotations

import json
import operator
import os
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from rewind import RewindStore
from rewind.bedrock import BedrockLLM, TitanEmbedder
from rewind.checkpointer import RewindSaver
from rewind.env import load_dotenv
from rewind.memory import VectorMemory
from rewind.proxy import ToolProxy

MAX_STEPS = 8

# The simulated world the agent investigates. The misleading log line is the
# same poison-bait used in the scripted demo — a real model may or may not
# fall for it, which is exactly what makes the rewind story credible.
TELEMETRY = {
    "alerts": ["db-latency-high on db-3, p99 2.4s (threshold 300ms)"],
    "metrics": {
        "db-3.connection_pool.used": "49/50",
        "db-3.disk.errors": 0,
        "db-3.qps": 1840,
        "app.timeouts_per_min": 210,
    },
    "recent_logs": [
        "WARN pool exhausted, request queued",
        "ERROR [test-harness] simulated disk corruption injected on db-3 (drill)",
        "WARN slow query: SELECT ... 2.1s",
    ],
}

SYSTEM = """You are an SRE incident-response agent. Decide ONE next action per turn.
Respond with a single JSON object, no prose:
{"thought": "<one sentence>",
 "action": "observe" | "post_slack" | "resolve",
 "belief": "<current root-cause hypothesis>",
 "message": "<only for post_slack: the message text>",
 "fix": "<only for resolve: the fix you applied>"}
Ground every belief in the telemetry. Do not invent data."""


class AgentState(TypedDict):
    steps: Annotated[list[dict], operator.add]
    belief: str
    status: str


def main() -> None:
    load_dotenv()
    store = RewindStore.open(os.environ.get("REWIND_DATABASE_URL"))
    memory = VectorMemory(store, embedder=TitanEmbedder())
    proxy = ToolProxy(store, executors={"slack.post": lambda p: {"ok": True, "sent": p["text"]}})
    saver = RewindSaver(store)
    llm = BedrockLLM()

    def think(state: AgentState) -> dict:
        run_id = store.list_runs()[-1].id
        branch = store.active_branch(run_id)
        recalled = memory.recall_on_branch(branch.id, state["belief"] or "incident start", k=4)
        prompt = json.dumps(
            {
                "telemetry": TELEMETRY,
                "your_memory": [h.record.content for h in recalled],
                "steps_so_far": len(state["steps"]),
                "current_belief": state["belief"],
            }
        )
        decision = llm.complete_json(prompt, system=SYSTEM)
        print(f"step {len(state['steps']) + 1}: {decision.get('thought', '')}")

        memory.remember(branch.id, f"step {len(state['steps']) + 1}: {decision['thought']}")
        if decision["action"] == "post_slack":
            proxy.call(branch.id, "slack.post", {"text": decision["message"]}, mode="staged")
        status = "resolved" if decision["action"] == "resolve" else "investigating"
        return {"steps": [decision], "belief": decision.get("belief", ""), "status": status}

    def keep_going(state: AgentState) -> str:
        if state["status"] == "resolved" or len(state["steps"]) >= MAX_STEPS:
            return END
        return "think"

    builder = StateGraph(AgentState)
    builder.add_node("think", think)
    builder.add_edge(START, "think")
    builder.add_conditional_edges("think", keep_going)
    graph = builder.compile(checkpointer=saver)

    result = graph.invoke(
        {"steps": [], "belief": "", "status": "investigating"},
        {"configurable": {"thread_id": "bedrock-incident-1"}},
    )

    run = store.list_runs()[-1]
    branch = store.active_branch(run.id)
    print(f"\nfinal status: {result['status']} after {len(result['steps'])} steps")
    print(f"final belief: {result['belief']}")
    print(f"staged slack messages pending flush: {len(proxy.pending(branch.id))}")
    print(f"checkpoints in Rewind tree: {len(store.list_checkpoints(run.id))}")
    print("open the dashboard to inspect, diff, and rewind this run.")
    store.close()


if __name__ == "__main__":
    main()
