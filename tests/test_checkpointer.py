import operator
from typing import Annotated, TypedDict

import pytest

pytest.importorskip("langgraph")

from langgraph.graph import END, START, StateGraph  # noqa: E402

from rewind import RewindStore  # noqa: E402
from rewind.checkpointer import RewindSaver  # noqa: E402


class State(TypedDict):
    count: int
    target: int
    log: Annotated[list[str], operator.add]


def bump(state: State) -> dict:
    n = state["count"] + 1
    return {"count": n, "log": [f"step {n}"]}


def loop_or_end(state: State) -> str:
    return "bump" if state["count"] < state["target"] else END


@pytest.fixture
def graph_and_store():
    store = RewindStore.open()
    saver = RewindSaver(store)
    builder = StateGraph(State)
    builder.add_node("bump", bump)
    builder.add_edge(START, "bump")
    builder.add_conditional_edges("bump", loop_or_end)
    graph = builder.compile(checkpointer=saver)
    yield graph, store, saver
    store.close()


def config(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}}


def state_at(graph, thread, count):
    return next(
        s for s in graph.get_state_history(config(thread)) if s.values.get("count") == count
    )


def test_langgraph_state_persists_through_rewind_store(graph_and_store):
    graph, store, saver = graph_and_store
    result = graph.invoke({"count": 0, "target": 3, "log": []}, config("t1"))
    assert result["count"] == 3
    assert result["log"] == ["step 1", "step 2", "step 3"]

    # State survives a brand-new saver over the same store (durability).
    fresh = RewindSaver(store)
    state = graph.get_state(config("t1"))
    assert state.values["count"] == 3
    assert (
        fresh.get_tuple(config("t1")).checkpoint["id"]
        == (state.config["configurable"]["checkpoint_id"])
    )

    # Every superstep landed in the Rewind tree.
    run = next(r for r in store.list_runs() if r.name == "langgraph:t1")
    lg_labels = [
        c.label for c in store.list_checkpoints(run.id) if (c.label or "").startswith("lg:")
    ]
    assert len(lg_labels) >= 4  # input checkpoint + 3 loop steps


def test_threads_are_isolated(graph_and_store):
    graph, store, saver = graph_and_store
    graph.invoke({"count": 0, "target": 2, "log": []}, config("a"))
    graph.invoke({"count": 10, "target": 11, "log": []}, config("b"))

    assert graph.get_state(config("a")).values["count"] == 2
    assert graph.get_state(config("b")).values["count"] == 11


def test_get_state_history_walks_lineage(graph_and_store):
    graph, store, saver = graph_and_store
    graph.invoke({"count": 0, "target": 3, "log": []}, config("t1"))

    history = list(graph.get_state_history(config("t1")))
    counts = [s.values["count"] for s in history if "count" in s.values]
    assert counts == sorted(counts, reverse=True)
    assert 3 in counts and 1 in counts


def test_langgraph_time_travel_forks_a_rewind_branch(graph_and_store):
    graph, store, saver = graph_and_store
    graph.invoke({"count": 0, "target": 3, "log": []}, config("t1"))

    # LangGraph-native time travel: resume from the count==1 checkpoint.
    target = state_at(graph, "t1", 1)
    result = graph.invoke(None, target.config)
    assert result["count"] == 3  # replayed steps 2 and 3

    # The replay was mirrored as a Rewind fork: old branch abandoned.
    run = next(r for r in store.list_runs() if r.name == "langgraph:t1")
    statuses = [b.status for b in store.list_branches(run.id)]
    assert statuses.count("abandoned") == 1
    assert statuses.count("active") == 1


def test_dashboard_rewind_resets_langgraph_state(graph_and_store):
    graph, store, saver = graph_and_store
    graph.invoke({"count": 0, "target": 3, "log": []}, config("t1"))
    assert graph.get_state(config("t1")).values["count"] == 3

    # Operator rewinds from the dashboard (pure Rewind API, no LangGraph).
    lg_id = state_at(graph, "t1", 1).config["configurable"]["checkpoint_id"]
    run = next(r for r in store.list_runs() if r.name == "langgraph:t1")
    branch = store.active_branch(run.id)
    target = next(c for c in store.list_checkpoints(run.id) if c.label == f"lg:{lg_id}")
    store.rewind(branch.id, target.id)

    # LangGraph sees the rewound state and resumes the loop from count==1.
    assert graph.get_state(config("t1")).values["count"] == 1
    result = graph.invoke(None, config("t1"))
    assert result["count"] == 3
    assert graph.get_state(config("t1")).values["log"] == [
        "step 1",
        "step 2",
        "step 3",
    ]
