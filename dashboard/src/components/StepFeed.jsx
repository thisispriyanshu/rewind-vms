import React, { useEffect, useRef } from "react";

// Keys rendered as chips under a feed card, in priority order.
const CHIP_KEYS = ["alert", "suspect", "evidence", "plan", "fix", "error", "p99_ms", "backup_age_hours"];

function chipsFor(delta) {
  return CHIP_KEYS.filter((k) => delta[k] !== undefined && delta[k] !== null).map((k) => ({
    key: k,
    value: String(delta[k]),
  }));
}

export default function StepFeed({ tree, deltas, selectedId, onSelect }) {
  const bottomRef = useRef(null);
  const count = tree?.checkpoints.length || 0;
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [count]);

  if (!tree) return null;
  const branchById = Object.fromEntries(tree.branches.map((b) => [b.id, b]));
  const byId = Object.fromEntries(tree.checkpoints.map((c) => [c.id, c]));

  // A step is "abandoned" only if it is NOT an ancestor of the active head —
  // shared history before the fork point still counts for the live timeline.
  const activeHead = tree.branches.find((b) => b.status === "active")?.head_checkpoint_id;
  const lineage = new Set();
  for (let c = byId[activeHead]; c; c = byId[c.parent_checkpoint_id]) lineage.add(c.id);

  const steps = tree.checkpoints
    .filter((c) => c.step_index > 0)
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1));

  return (
    <div className="panel feed-panel">
      <div className="panel-caption">
        <span className="caption-title">Agent activity</span>
        <span className="caption-sub">every step is a checkpoint — click to inspect</span>
      </div>
      <div className="feed-scroll">
        {steps.length === 0 && (
          <div className="hint-text pad">The agent's reasoning will stream here…</div>
        )}
        {steps.map((c) => {
          const delta = deltas[c.id] || {};
          const branch = branchById[c.branch_id];
          const abandoned = branch?.status === "abandoned" && !lineage.has(c.id);
          const failed = delta.status === "failed";
          const resolved = delta.status === "resolved";
          const cls = [
            "feed-card",
            abandoned ? "abandoned" : "",
            failed ? "failed" : "",
            resolved ? "resolved" : "",
            selectedId === c.id ? "selected" : "",
          ].join(" ");
          return (
            <div key={c.id} className={cls} onClick={() => onSelect(c.id)}>
              <div className="feed-head">
                <span className="step-no">step {c.step_index}</span>
                <span className="feed-label">{c.label}</span>
                {abandoned && <span className="tag tag-grey">abandoned</span>}
                {failed && !abandoned && <span className="tag tag-red">FAILED</span>}
                {resolved && <span className="tag tag-green">RESOLVED</span>}
              </div>
              {delta.thought && <div className="thought">“{delta.thought}”</div>}
              <div className="chips">
                {chipsFor(delta).map(({ key, value }) => (
                  <span key={key} className={`chip ${key === "error" ? "chip-red" : ""}`}>
                    <b>{key}</b> {value.length > 64 ? value.slice(0, 64) + "…" : value}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
