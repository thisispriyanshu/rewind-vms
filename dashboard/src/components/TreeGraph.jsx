import React from "react";

const X_STEP = 128;
const Y_LANE = 92;
const X0 = 120;
const Y0 = 62;
const R = 16;

function nodeColor(ckpt, branch, states) {
  const status = states[ckpt.id]?.status;
  if (branch?.status === "abandoned") {
    return status === "failed" ? "var(--red-dim)" : "var(--grey)";
  }
  if (status === "failed") return "var(--red)";
  if (status === "resolved") return "var(--green)";
  if (ckpt.step_index === 0) return "var(--grey-light)";
  return "var(--blue)";
}

export default function TreeGraph({ tree, states, selectedId, onSelect }) {
  const branches = tree.branches;
  const checkpoints = tree.checkpoints;
  const laneOf = Object.fromEntries(branches.map((b, i) => [b.id, i]));
  const byId = Object.fromEntries(checkpoints.map((c) => [c.id, c]));

  const pos = (c) => ({
    x: X0 + c.step_index * X_STEP,
    y: Y0 + laneOf[c.branch_id] * Y_LANE,
  });

  const width = X0 + (Math.max(0, ...checkpoints.map((c) => c.step_index)) + 1) * X_STEP;
  const height = Y0 + branches.length * Y_LANE - 18;

  return (
    <svg
      className="tree"
      width={Math.max(width, 900)}
      height={Math.max(height, 150)}
      role="img"
      aria-label="Checkpoint tree"
    >
      {branches.map((b, i) => (
        <g key={b.id}>
          <circle
            cx={18}
            cy={Y0 + i * Y_LANE - 10}
            r={4}
            fill={b.status === "abandoned" ? "var(--grey)" : "var(--green)"}
          />
          <text x={28} y={Y0 + i * Y_LANE - 6} className={`lane-label ${b.status}`}>
            {b.name}
          </text>
          <text x={14} y={Y0 + i * Y_LANE + 8} className="lane-sub">
            {b.status === "abandoned" ? "abandoned timeline" : "active timeline"}
          </text>
        </g>
      ))}

      {checkpoints.map((c) => {
        const parent = byId[c.parent_checkpoint_id];
        if (!parent) return null;
        const p1 = pos(parent);
        const p2 = pos(c);
        const abandoned = branches.find((b) => b.id === c.branch_id)?.status === "abandoned";
        const d =
          p1.y === p2.y
            ? `M ${p1.x + R} ${p1.y} L ${p2.x - R} ${p2.y}`
            : `M ${p1.x} ${p1.y + R} C ${p1.x} ${p2.y}, ${p1.x} ${p2.y}, ${p2.x - R} ${p2.y}`;
        return <path key={c.id} d={d} className={`edge ${abandoned ? "abandoned" : ""}`} />;
      })}

      {checkpoints.map((c) => {
        const branch = branches.find((b) => b.id === c.branch_id);
        const { x, y } = pos(c);
        const isHead = branch?.head_checkpoint_id === c.id;
        const failed = states[c.id]?.status === "failed" && branch?.status === "active";
        return (
          <g
            key={c.id}
            transform={`translate(${x}, ${y})`}
            className={`node ${branch?.status} ${selectedId === c.id ? "selected" : ""}`}
            onClick={() => onSelect(c.id)}
          >
            {selectedId === c.id && <circle r={R + 6} className="select-ring" />}
            {failed && <circle r={R + 3} className="fail-glow" fill="none" />}
            <circle r={R} fill={nodeColor(c, branch, states)} />
            <text y={4.5} className="node-step">
              {c.step_index}
            </text>
            <text y={R + 15} className="node-label">
              {(c.label || "").slice(0, 24)}
            </text>
            {isHead && branch?.status === "active" && (
              <circle r={R} className="head-pulse" fill="none" />
            )}
          </g>
        );
      })}
    </svg>
  );
}
