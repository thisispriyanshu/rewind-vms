import React from "react";

const X_STEP = 120;
const Y_LANE = 96;
const X0 = 90;
const Y0 = 70;
const R = 17;

function nodeColor(ckpt, branch, states) {
  const status = states[ckpt.id]?.status;
  if (branch?.status === "abandoned") {
    return status === "failed" ? "var(--red)" : "var(--grey)";
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
  const height = Y0 + branches.length * Y_LANE;

  return (
    <svg
      className="tree"
      width={Math.max(width, 700)}
      height={Math.max(height, 240)}
      role="img"
      aria-label="Checkpoint tree"
    >
      {branches.map((b, i) => (
        <text key={b.id} x={12} y={Y0 + i * Y_LANE + 5} className={`lane-label ${b.status}`}>
          {b.name}
        </text>
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
        return (
          <g
            key={c.id}
            transform={`translate(${x}, ${y})`}
            className={`node ${branch?.status} ${selectedId === c.id ? "selected" : ""}`}
            onClick={() => onSelect(c.id)}
          >
            {selectedId === c.id && <circle r={R + 6} className="select-ring" />}
            <circle r={R} fill={nodeColor(c, branch, states)} />
            <text y={5} className="node-step">
              {c.step_index}
            </text>
            <text y={R + 16} className="node-label">
              {(c.label || "").slice(0, 22)}
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
