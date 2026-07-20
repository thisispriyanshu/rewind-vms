import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import Inspector from "./components/Inspector.jsx";
import SlackPanel from "./components/SlackPanel.jsx";
import StepFeed from "./components/StepFeed.jsx";
import Toast from "./components/Toast.jsx";
import TreeGraph from "./components/TreeGraph.jsx";

const POLL_MS = 1500;

const STATUS_META = {
  investigating: { label: "INVESTIGATING", cls: "blue" },
  escalating: { label: "ESCALATING", cls: "amber" },
  failed: { label: "FAILED", cls: "red" },
  resolved: { label: "RESOLVED", cls: "green" },
};

export default function App() {
  const [runId, setRunId] = useState(null);
  const [tree, setTree] = useState(null);
  const [states, setStates] = useState({});
  const [deltas, setDeltas] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [toast, setToast] = useState(null);
  const [starting, setStarting] = useState(false);
  const deltaCache = useRef({});

  const activeBranch = useMemo(
    () => tree?.branches.find((b) => b.status === "active"),
    [tree],
  );
  const headId = activeBranch?.head_checkpoint_id;
  const headState = states[headId] || {};

  useEffect(() => {
    if (!runId) return undefined;
    let live = true;
    const tick = async () => {
      try {
        const t = await api.tree(runId);
        if (!live) return;
        setTree(t);
        const stateEntries = await Promise.all(
          t.checkpoints.map(async (c) => [c.id, await api.state(c.id)]),
        );
        // Deltas are immutable per checkpoint — fetch each exactly once.
        const missing = t.checkpoints.filter((c) => !deltaCache.current[c.id]);
        const deltaEntries = await Promise.all(
          missing.map(async (c) => [c.id, await api.delta(c.id)]),
        );
        for (const [id, d] of deltaEntries) deltaCache.current[id] = d;
        if (!live) return;
        setStates(Object.fromEntries(stateEntries));
        setDeltas({ ...deltaCache.current });
      } catch {
        /* transient poll failure is fine */
      }
    };
    tick();
    const interval = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(interval);
    };
  }, [runId]);

  const startDemo = useCallback(async () => {
    setStarting(true);
    try {
      const { run_id: id } = await api.startDemo(2.0);
      deltaCache.current = {};
      setSelectedId(null);
      setTree(null);
      setStates({});
      setDeltas({});
      setRunId(id);
      setToast({ text: "Incident agent started — watch it reason.", kind: "info" });
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
          ? `⏪ Rewound. ${n} pending Slack message${n === 1 ? "" : "s"} on the abandoned timeline discarded — they were never sent.`
          : "⏪ Rewound to a clean checkpoint.",
      kind: "success",
    });
    setSelectedId(null);
  }, [activeBranch, selectedId]);

  const status = STATUS_META[headState.status] || null;
  const failed = headState.status === "failed";
  const selected = tree?.checkpoints.find((c) => c.id === selectedId);

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="brand-mark">⏪</span> Rewind
          <span className="brand-sub">version control &amp; staging for AI agents</span>
        </div>
        <div className="header-right">
          {tree && status && (
            <span className={`status-pill ${status.cls}`}>{status.label}</span>
          )}
          {tree && headState.p99_ms !== undefined && (
            <span className={`badge ${headState.p99_ms > 300 ? "bad" : "good"}`}>
              p99 {headState.p99_ms}ms
            </span>
          )}
          <button className="start-btn" onClick={startDemo} disabled={starting}>
            {runId ? "Restart demo" : "▶ Start incident demo"}
          </button>
        </div>
      </header>

      {tree && (
        <div className="incident-strip">
          <span className="incident-icon">🚨</span>
          <span className="incident-title">
            {headState.alert || "checkout-api latency incident"}
          </span>
          <span className="caption-sub">
            an autonomous SRE agent is handling this incident — every step it takes is
            checkpointed in CockroachDB
          </span>
        </div>
      )}

      {failed && (
        <div className="alert">
          <b>The agent is stuck on a poisoned belief.</b> It misread a chaos-drill log line at
          step 3 and everything after is built on that mistake. Click the last clean checkpoint
          (step 2, “form hypothesis”), then hit <b>⏪ Rewind &amp; branch here</b>.
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
            <div className="columns">
              <StepFeed
                tree={tree}
                deltas={deltas}
                selectedId={selectedId}
                onSelect={setSelectedId}
              />
              <Inspector tree={tree} selected={selected} headId={headId} onRewind={rewind} />
              <SlackPanel tree={tree} />
            </div>
          </>
        ) : (
          <div className="empty">
            <h2>What if you could undo an AI agent's mistake?</h2>
            <p>
              An autonomous SRE agent will handle a simulated production incident. Watch it
              checkpoint every step, poison its own memory by misreading a log line, and spiral
              toward a destructive fix — then <b>rewind it</b> to the moment before it went
              wrong. Its staged side effects are discarded before the real world ever sees
              them, and it recovers on a fresh timeline.
            </p>
            <button className="start-btn big" onClick={startDemo} disabled={starting}>
              ▶ Start the incident
            </button>
          </div>
        )}
      </main>

      <Toast toast={toast} onDone={() => setToast(null)} />
    </div>
  );
}
