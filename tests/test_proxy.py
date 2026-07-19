import pytest

from rewind import RewindStore
from rewind.proxy import ToolProxy


@pytest.fixture
def store():
    s = RewindStore.open()
    yield s
    s.close()


@pytest.fixture
def env(store):
    run = store.create_run("proxy-run")
    branch = store.active_branch(run.id)
    sent: list[dict] = []

    def slack_executor(payload):
        sent.append(payload)
        return {"ok": True, "message_id": f"m{len(sent)}"}

    proxy = ToolProxy(store, executors={"slack.post": slack_executor})
    return store, run, branch, proxy, sent


def test_staged_effect_does_not_execute_until_flush(env):
    store, run, branch, proxy, sent = env
    outcome = proxy.call(branch.id, "slack.post", {"text": "hello"}, mode="staged")

    assert outcome.executed is False
    assert sent == []  # nothing left the sandbox
    assert [e.tool_name for e in proxy.pending(branch.id)] == ["slack.post"]

    flushed = proxy.flush(branch.id)
    assert sent == [{"text": "hello"}]
    assert flushed[0].effect.status == "committed"
    assert flushed[0].result["ok"] is True
    assert proxy.pending(branch.id) == []


def test_rewind_discards_staged_effects_and_they_never_fire(env):
    store, run, branch, proxy, sent = env
    clean = store.create_checkpoint(branch.id, {"step": 1})

    store.create_checkpoint(branch.id, {"step": 2})
    proxy.call(branch.id, "slack.post", {"text": "prod is down!"}, mode="staged")
    store.create_checkpoint(branch.id, {"step": 3})
    proxy.call(branch.id, "slack.post", {"text": "escalating to CEO"}, mode="staged")
    proxy.call(branch.id, "slack.post", {"text": "paging everyone"}, mode="staged")

    result = store.rewind(branch.id, clean.id)
    assert len(result.discarded_effects) == 3  # the demo toast number

    # Flushing the fresh branch sends nothing: the discarded messages are gone.
    assert proxy.flush(result.new_branch.id) == []
    assert sent == []


def test_staged_effect_before_rewind_point_survives_and_flushes(env):
    store, run, branch, proxy, sent = env
    store.create_checkpoint(branch.id, {"step": 1})
    proxy.call(branch.id, "slack.post", {"text": "started investigation"}, mode="staged")
    clean = store.get_branch(branch.id).head_checkpoint_id

    store.create_checkpoint(branch.id, {"step": 2})
    proxy.call(branch.id, "slack.post", {"text": "bad conclusion"}, mode="staged")

    new_branch = store.rewind(branch.id, clean).new_branch
    flushed = proxy.flush(new_branch.id)

    assert [o.effect.payload["text"] for o in flushed] == ["started investigation"]
    assert sent == [{"text": "started investigation"}]


def test_idempotent_executes_once_per_key(env):
    store, run, branch, proxy, sent = env
    first = proxy.call(
        branch.id, "slack.post", {"text": "ticket"}, mode="idempotent", idempotency_key="t-1"
    )
    second = proxy.call(
        branch.id, "slack.post", {"text": "ticket"}, mode="idempotent", idempotency_key="t-1"
    )

    assert first.executed is True
    assert second.executed is False
    assert second.result == first.result
    assert len(sent) == 1


def test_idempotent_replay_after_rewind_is_safe(env):
    store, run, branch, proxy, sent = env
    clean = store.create_checkpoint(branch.id, {"step": 1})
    proxy.register("charge.card", lambda p: {"charged": p["amount"]})
    # First real charge:
    outcome = proxy.call(
        branch.id, "charge.card", {"amount": 99}, mode="idempotent", idempotency_key="chg-9"
    )
    assert outcome.executed is True

    new_branch = store.rewind(branch.id, clean.id).new_branch
    # Agent replays the same step on the new branch — same key, no double charge.
    replay = proxy.call(
        new_branch.id, "charge.card", {"amount": 99}, mode="idempotent", idempotency_key="chg-9"
    )
    assert replay.executed is False
    assert replay.result == {"charged": 99}


def test_idempotent_requires_key(env):
    store, run, branch, proxy, sent = env
    with pytest.raises(ValueError, match="idempotency_key"):
        proxy.call(branch.id, "slack.post", {"text": "x"}, mode="idempotent")


def test_dry_run_never_touches_executor(env):
    store, run, branch, proxy, sent = env
    outcome = proxy.call(branch.id, "slack.post", {"text": "test"}, mode="dry_run")
    assert outcome.result["dry_run"] is True
    assert sent == []
    assert proxy.pending(branch.id) == []  # dry runs are not pending work


def test_unknown_mode_rejected(env):
    store, run, branch, proxy, sent = env
    with pytest.raises(ValueError, match="unknown proxy mode"):
        proxy.call(branch.id, "slack.post", {}, mode="yolo")


def test_flush_without_executor_raises(store):
    run = store.create_run("r")
    branch = store.active_branch(run.id)
    proxy = ToolProxy(store)
    proxy.call(branch.id, "unknown.tool", {}, mode="staged")
    with pytest.raises(KeyError, match="unknown.tool"):
        proxy.flush(branch.id)
