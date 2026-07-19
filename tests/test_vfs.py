import pytest

from rewind import RewindStore
from rewind.vfs import VFS


@pytest.fixture
def store():
    s = RewindStore.open()
    yield s
    s.close()


@pytest.fixture
def env(store):
    run = store.create_run("vfs-run")
    branch = store.active_branch(run.id)
    return store, run, branch, VFS(store)


def test_write_and_read(env):
    store, run, branch, vfs = env
    ckpt = vfs.write(branch.id, "/notes/plan.md", "step 1: look at logs")
    assert vfs.read_text(ckpt, "/notes/plan.md") == "step 1: look at logs"
    assert vfs.exists(ckpt, "/notes/plan.md")


def test_binary_content(env):
    store, run, branch, vfs = env
    payload = bytes(range(256))
    ckpt = vfs.write(branch.id, "/blob.bin", payload)
    assert vfs.read(ckpt, "/blob.bin") == payload


def test_overwrite_keeps_history(env):
    store, run, branch, vfs = env
    c1 = vfs.write(branch.id, "/config.yaml", "retries: 3")
    c2 = vfs.write(branch.id, "/config.yaml", "retries: 5")
    assert vfs.read_text(c1, "/config.yaml") == "retries: 3"
    assert vfs.read_text(c2, "/config.yaml") == "retries: 5"


def test_delete_is_tombstone(env):
    store, run, branch, vfs = env
    c1 = vfs.write(branch.id, "/tmp.txt", "scratch")
    c2 = vfs.delete(branch.id, "/tmp.txt")
    assert not vfs.exists(c2, "/tmp.txt")
    with pytest.raises(FileNotFoundError):
        vfs.read(c2, "/tmp.txt")
    # Still readable at the earlier checkpoint.
    assert vfs.read_text(c1, "/tmp.txt") == "scratch"


def test_list_with_prefix(env):
    store, run, branch, vfs = env
    vfs.write(branch.id, "/logs/a.log", "a")
    vfs.write(branch.id, "/logs/b.log", "b")
    ckpt = vfs.write(branch.id, "/notes.md", "n")
    listed = vfs.list(ckpt, prefix="/logs/")
    assert [f.path for f in listed] == ["/logs/a.log", "/logs/b.log"]
    assert len(vfs.list(ckpt)) == 3


def test_files_commit_atomically_with_state(env):
    store, run, branch, vfs = env
    ckpt = store.create_checkpoint(
        branch.id,
        updates={"step": "wrote report"},
        files={"/report.md": b"# Findings"},
        label="step 4",
    )
    assert store.get_value(ckpt.id, "step") == "wrote report"
    assert vfs.read_text(ckpt.id, "/report.md") == "# Findings"


def test_rewind_restores_files(env):
    store, run, branch, vfs = env
    good = vfs.write(branch.id, "/runbook.md", "restart service A")
    vfs.write(branch.id, "/runbook.md", "DELETE PROD DATABASE")  # poisoned

    new_branch = store.rewind(branch.id, good).new_branch
    assert vfs.read_text(new_branch.head_checkpoint_id, "/runbook.md") == "restart service A"

    # The new branch continues cleanly.
    fixed = vfs.write(new_branch.id, "/runbook.md", "restart service A, then verify")
    assert vfs.read_text(fixed, "/runbook.md") == "restart service A, then verify"
