import React from "react";

function relTime(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function RunList({ runs, activeRunId, activeStatus, onSelect, onLaunch, launching }) {
  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <span>Runs</span>
        <span className="count">{runs.length}</span>
      </div>
      <div className="run-list">
        {runs.length === 0 && <div className="hint-text pad">No agent runs yet.</div>}
        {[...runs].reverse().map((r) => {
          const active = r.id === activeRunId;
          return (
            <button
              key={r.id}
              className={`run-item ${active ? "active" : ""}`}
              onClick={() => onSelect(r.id)}
            >
              <span className="run-name mono">{r.name}</span>
              <span className="run-meta">
                {active && activeStatus && (
                  <span className={`dot ${activeStatus}`} title={activeStatus} />
                )}
                {relTime(r.created_at)}
              </span>
            </button>
          );
        })}
      </div>
      <button className="launch-btn" onClick={onLaunch} disabled={launching}>
        {launching ? "Launching…" : "Launch sample incident agent"}
      </button>
    </aside>
  );
}
