import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import DiffPanel from "./components/DiffPanel.jsx";
import Toast from "./components/Toast.jsx";
import TreeGraph from "./components/TreeGraph.jsx";

const POLL_MS = 1500;

export default function App() {
  const [runId, setRunId] = useState(null);
  const [tree, setTree] = useState(null);
  const [states, setStates] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [selectedState, setSelectedState] = useState(null);
  const [diff, setDiff] = useState(null);
  const [pending, setPending] = useState([]);
  const [sent, setSent] = useState([]);
  const [toast, setToast] = useState(null);
  const [starting, setStarting] = useState(false);

  const activeBranch = useMemo(
    () => tree?.branches.find((b) => b.status === "active"),
    [tree],
  );
  const headId = activeBranch?.head_checkpoint_id;

  // Poll the tree (and per-checkpoint status colors) while a run is active.
  useEffect(() => {
    if (!runId) return undefined;
    let live = true;
    const tick = async () => {
      try {
        const t = await api.tree(runId);
        if (!live) return;
        setTree(t);
        const entries = await Promise.all(
          t.checkpoints.map(async (c) => [c.id, await api.state(c.id)]),
        );
        if (!live) return;
        setStates(Object.fromEntries(entries));
        const active = t.branches.find((b) => b.status === "active");
        if (active) setPending(await api.pendingEffects(active.id));
        setSent(await api.sentMessages(runId));
      } catch {
        /* server restarting between polls is fine */
      }
    };
    tick();
    const interval = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(interval);
    };
  }, [runId]);

  // Load details for the selected node.
  useEffect(() => {
    if (!selectedId || !headId) {
      setSelectedState(null);
      setDiff(null);
      return;
    }
    (async () => {
      try {
        setSelectedState(await api.state(selectedId));
        setDiff(selectedId === headId ? {} : await api.diff(selectedId, headId));
      } catch {
        /* node may belong to a stale poll */
      }
    })();
  }, [selectedId, headId]);

  const startDemo = useCallback(async () => {
    setStarting(true);
    try {
      const { run_id: id } = await api.startDemo(2.0);
      setSelectedId(null);
      setTree(null);
      setRunId(id);
      setToast({ text: "Incident agent started — watch it work.", kind: "info" });
    } finally {
      setStarting(false);
    }
  }, []);

  const rewind = useCallback(async () => {
    if (!activeBranch || !selectedId) return;
    const result = await api.rewind(activeBranch.id, selectedId);
    const n = result.discarded_count;
    setToast({
      text:
        n > 0
          ? `⏪ Rewound. ${n} pending Slack message${n === 1 ? "" : "s"} on the abandoned branch discarded.`
          : "⏪ Rewound to a clean checkpoint.",
      kind: "success",
    });
    setSelectedId(null);
  }, [activeBranch, selectedId]);

  const failed = headId && states[headId]?.status === "failed";

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="brand-mark">⏪</span> Rewind
          <span className="brand-sub">Agent Time-Travel Dashboard</span>
        </div>
        <div className="header-right">
          {pending.length > 0 && (
            <span className="badge pending">{pending.length} staged effects pending</span>
          )}
          {sent.length > 0 && <span className="badge sent">{sent.length} sent for real</span>}
          <button className="start-btn" onClick={startDemo} disabled={starting}>
            {runId ? "Restart demo" : "Start incident demo"}
          </button>
        </div>
      </header>

      {failed && (
        <div className="alert">
          ⚠ The agent failed on branch “{activeBranch.name}”. Pick the last clean checkpoint and
          rewind.
        </div>
      )}

      <main>
        {tree ? (
          <>
            <div className="tree-wrap">
              <TreeGraph
                tree={tree}
                states={states}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
            </div>
            <DiffPanel
              selected={tree.checkpoints.find((c) => c.id === selectedId)}
              selectedState={selectedState}
              headState={states[headId]}
              diff={diff}
              canRewind={!!selectedId && selectedId !== headId && !!activeBranch}
              onRewind={rewind}
            />
          </>
        ) : (
          <div className="empty">
            <h2>Version control for AI agents</h2>
            <p>
              Start the demo: an incident-response agent will checkpoint every step, poison its own
            memory at step 3, and fail — then you rewind it in front of your eyes.
            </p>
          </div>
        )}
      </main>

      <Toast toast={toast} onDone={() => setToast(null)} />
    </div>
  );
}
