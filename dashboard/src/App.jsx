import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api.js";
import HeroExplainer from "./components/HeroExplainer.jsx";
import Inspector from "./components/Inspector.jsx";
import RewindModal from "./components/RewindModal.jsx";
import RunList from "./components/RunList.jsx";
import SlackPanel from "./components/SlackPanel.jsx";
import Sparkline from "./components/Sparkline.jsx";
import StepFeed from "./components/StepFeed.jsx";
import Toast from "./components/Toast.jsx";
import TreeGraph from "./components/TreeGraph.jsx";

const POLL_MS = 1500;

const STATUS_META = {
  investigating: { label: "INVESTIGATING", cls: "blue" },
  escalating: { label: "ESCALATING", cls: "amber" },
  failed: { label: "FAILED ON POISONED BELIEF", cls: "red" },
  resolved: { label: "RESOLVED ON RECOVERY BRANCH", cls: "green" },
};

const SEV_LABEL = { 1: "SEV-1", 2: "SEV-2", 3: "SEV-3", 4: "SEV-4" };

function hashRunId() {
  const m = window.location.hash.match(/#\/runs\/([a-f0-9]+)/);
  return m ? m[1] : null;
}

export default function App() {
  const [runs, setRuns] = useState([]);
  const [meta, setMeta] = useState(null);
  const [pollMs, setPollMs] = useState(null);
  const [runId, setRunId] = useState(hashRunId());
  const [tree, setTree] = useState(null);
  const [states, setStates] = useState({});
  const [deltas, setDeltas] = useState({});
  const [selectedId, setSelectedId] = useState(null);
  const [toast, setToast] = useState(null);
  const [launching, setLaunching] = useState(false);
  const [rewindPlan, setRewindPlan] = useState(null);
  const [rewinding, setRewinding] = useState(false);
  const deltaCache = useRef({});

  const activeBranch = useMemo(
    () => tree?.branches.find((b) => b.status === "active"),
    [tree],
  );
  const headId = activeBranch?.head_checkpoint_id;
  const headState = states[headId] || {};

  // One-time metadata + runs list polling.
  useEffect(() => {
    api.meta().then(setMeta).catch(() => {});
    const load = () => api.listRuns().then(setRuns).catch(() => {});
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const onHash = () => {
      const id = hashRunId();
      if (id && id !== runId) selectRun(id);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });

  // Poll the selected run.
  useEffect(() => {
    if (!runId) return undefined;
    let live = true;
    const tick = async () => {
      try {
        const t0 = performance.now();
        const t = await api.tree(runId);
        if (!live) return;
        setPollMs(Math.round(performance.now() - t0));
        setTree(t);
        const stateEntries = await Promise.all(
          t.checkpoints.map(async (c) => [c.id, await api.state(c.id)]),
        );
        const missing = t.checkpoints.filter((c) => !deltaCache.current[c.id]);
        const deltaEntries = await Promise.all(
          missing.map(async (c) => [c.id, await api.delta(c.id)]),
        );
        for (const [id, d] of deltaEntries) deltaCache.current[id] = d;
        if (!live) return;
        setStates(Object.fromEntries(stateEntries));
        setDeltas({ ...deltaCache.current });
      } catch {
        /* transient poll failure */
      }
    };
    tick();
    const interval = setInterval(tick, POLL_MS);
    return () => {
      live = false;
      clearInterval(interval);
    };
  }, [runId]);

  const selectRun = (id) => {
    deltaCache.current = {};
    setSelectedId(null);
    setTree(null);
    setStates({});
    setDeltas({});
    setRunId(id);
    window.location.hash = `#/runs/${id}`;
  };

  const launch = useCallback(async () => {
    setLaunching(true);
    try {
      const { run_id: id } = await api.startDemo(2.0);
      const fresh = await api.listRuns();
      setRuns(fresh);
      selectRun(id);
    } finally {
      setLaunching(false);
    }
  }, []);

  // Build the rewind confirmation plan client-side from the tree we have.
  const openRewind = useCallback(() => {
    if (!tree || !selectedId) return;
    const byId = Object.fromEntries(tree.checkpoints.map((c) => [c.id, c]));
    const targetLineage = new Set();
    for (let c = byId[selectedId]; c; c = byId[c.parent_checkpoint_id]) targetLineage.add(c.id);
    const headLineage = [];
    for (let c = byId[headId]; c; c = byId[c.parent_checkpoint_id]) headLineage.push(c.id);
    const abandoned = headLineage.filter((id) => !targetLineage.has(id));
    const discardCount = (tree.effects || []).filter(
      (e) => e.status === "staged" && abandoned.includes(e.checkpoint_id),
    ).length;
    setRewindPlan({
      target: byId[selectedId],
      abandonedSteps: abandoned.length,
      discardCount,
    });
  }, [tree, selectedId, headId]);

  const confirmRewind = useCallback(async () => {
    if (!activeBranch || !rewindPlan) return;
    setRewinding(true);
    try {
      const result = await api.rewind(activeBranch.id, rewindPlan.target.id);
      const n = result.discarded_count;
      setToast({
        text:
          n > 0
            ? `⏪ Rewound to ${rewindPlan.target.id.slice(0, 8)}. ${n} staged side effect${n === 1 ? "" : "s"} discarded — never reached Slack!`
            : `⏪ Rewound to ${rewindPlan.target.id.slice(0, 8)}. Fresh branch initialized.`,
        kind: "success",
      });
      setSelectedId(null);
      setRewindPlan(null);
    } finally {
      setRewinding(false);
    }
  }, [activeBranch, rewindPlan]);

  const status = STATUS_META[headState.status] || null;
  const failed = headState.status === "failed";
  const selected = tree?.checkpoints.find((c) => c.id === selectedId);

  // p99 series along the active lineage, for the header sparkline.
  const p99Series = useMemo(() => {
    if (!tree || !headId) return [];
    const byId = Object.fromEntries(tree.checkpoints.map((c) => [c.id, c]));
    const chain = [];
    for (let c = byId[headId]; c; c = byId[c.parent_checkpoint_id]) chain.unshift(c.id);
    return chain.map((id) => states[id]?.p99_ms).filter((v) => typeof v === "number");
  }, [tree, headId, states]);

  return (
    <div className="app">
      <header>
        <div className="brand">
          <svg className="logo" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true">
            <rect x="1" y="1" width="22" height="22" rx="6" fill="var(--accent)" />
            <path
              d="M13.5 6.5 8 12l5.5 5.5M18 6.5 12.5 12l5.5 5.5"
              stroke="#fff"
              strokeWidth="2.2"
              strokeLinecap="round"
              strokeLinejoin="round"
              fill="none"
            />
          </svg>
          <span className="brand-title">Rewind VMS</span>
          <span className="brand-sub">Version Control &amp; Staging for AI Agents</span>
        </div>

        <div className="header-integrations hide-mobile">
          <span className="header-chip crdb">🪳 CockroachDB Vector Memory</span>
          <span className="header-chip aws">☁️ AWS Bedrock + Lambda</span>
          <span className="header-chip mcp">🔌 Managed MCP</span>
        </div>

        <div className="header-right">
          {tree && status && <span className={`status-pill ${status.cls}`}>{status.label}</span>}
          {tree && headState.severity && (
            <span className="badge sev">{SEV_LABEL[headState.severity] || "SEV-?"}</span>
          )}
        </div>
      </header>

      <div className="shell">
        <RunList
          runs={runs}
          activeRunId={runId}
          activeStatus={headState.status}
          onSelect={selectRun}
          onLaunch={launch}
          launching={launching}
        />

        <main>
          <HeroExplainer />

          {tree ? (
            <>
              <div className="incident-strip">
                <div className="incident-left">
                  <span className="incident-title">
                    🚨 {headState.alert || tree.run.name}
                  </span>
                  <span className="caption-sub">
                    Agent Run ID: <span className="mono">{tree.run.name}</span> · Checkpoints committed to CockroachDB
                  </span>
                </div>
                {p99Series.length > 1 && (
                  <div className="incident-metric">
                    <Sparkline points={p99Series} slo={300} />
                    <div className="metric-label">
                      <span className={`metric-value mono ${headState.p99_ms > 300 ? "bad" : "good"}`}>
                        {headState.p99_ms}ms
                      </span>
                      <span className="caption-sub">p99 Latency · SLO 300ms</span>
                    </div>
                  </div>
                )}
              </div>

              {failed && (
                <div className="alert-banner">
                  <div className="alert-content">
                    <span className="alert-icon">⚠️</span>
                    <div>
                      <b>Agent Trapped in Poisoned Reasoning Loop:</b> At step 3, the agent misread a chaos-drill test line as real disk corruption. It scheduled 3 urgent Slack alerts and prepared to wipe <code>db-3</code>.
                      <br />
                      <b>How to Fix:</b> Click on <b>Step 2 ("form hypothesis")</b> in the tree below, then click <b>⏪ Rewind &amp; Branch Here</b>. Watch how Rewind discards all 3 staged Slack alerts before they ever fire!
                    </div>
                  </div>
                </div>
              )}

              <div className="tree-wrap">
                <div className="tree-header">
                  <span className="tree-header-title">🌿 Agent Timeline &amp; Branching Graph</span>
                  <span className="tree-header-sub">Solid line: Active timeline | Dashed line: Abandoned poisoned branch (preserved for audit)</span>
                </div>
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
                <Inspector tree={tree} selected={selected} headId={headId} onRewind={openRewind} />
                <SlackPanel tree={tree} />
              </div>
            </>
          ) : (
            <div className="landing-card">
              <div className="landing-hero">
                <span className="hero-pill">COCKROACHDB × AWS HACKATHON</span>
                <h2>Version Control &amp; Staging for AI Agents</h2>
                <p>
                  Deploying AI agents today is like letting developers push straight to production with no Git, no local server, and no code review. <b>Rewind</b> turns agent memory into a versioned, rewindable system of record.
                </p>
                <div className="landing-actions">
                  <button className="start-btn big glowing" onClick={launch} disabled={launching}>
                    {launching ? "Launching Incident Agent…" : "🚀 Launch Incident Response Agent Demo"}
                  </button>
                </div>
              </div>

              <div className="landing-comparison">
                <div className="comp-col traditional">
                  <h3>❌ Traditional Agent Execution</h3>
                  <ul>
                    <li>Single hallucination poisons all downstream agent reasoning</li>
                    <li>External API calls (Slack, DB drops) execute immediately</li>
                    <li>No way to roll back state without restarting from scratch</li>
                    <li>Memory vector store is write-only black box</li>
                  </ul>
                </div>
                <div className="comp-col rewind">
                  <h3>✅ Rewind VMS Architecture</h3>
                  <ul>
                    <li><b>Atomic CockroachDB Checkpoints:</b> State, files &amp; vector memory</li>
                    <li><b>Idempotent Tool Proxy:</b> Side-effects staged &amp; safely discarded on rewind</li>
                    <li><b>Non-Destructive Rewind:</b> Fork clean checkpoint onto new branch</li>
                    <li><b>Lineaged Vector Recall:</b> Memory rolls back with state</li>
                  </ul>
                </div>
              </div>
            </div>
          )}
        </main>
      </div>

      <footer className="statusbar">
        <div className="status-left">
          <span className={`dot ${meta ? "resolved" : "failed"}`} />
          {meta
            ? `Backend: ${meta.backend} ${meta.version} · Cluster: ${meta.host}`
            : "Connecting to Backend…"}
        </div>
        <div className="status-right mono">
          {pollMs !== null ? `Sync: ${pollMs}ms` : ""}
        </div>
      </footer>

      <RewindModal
        plan={rewindPlan}
        busy={rewinding}
        onCancel={() => setRewindPlan(null)}
        onConfirm={confirmRewind}
      />
      <Toast toast={toast} onDone={() => setToast(null)} />
    </div>
  );
}
