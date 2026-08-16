import React from "react";

// Single-series p99 sparkline with a dashed SLO reference. One series → no
// legend; identity comes from the adjacent label, status from the badge text.
export default function Sparkline({ points, slo = 300, width = 132, height = 30 }) {
  if (!points || points.length < 2) return null;
  const max = Math.max(...points, slo) * 1.12;
  const px = (i) => 2 + (i / (points.length - 1)) * (width - 4);
  const py = (v) => height - 3 - (v / max) * (height - 6);
  const path = points.map((v, i) => `${i === 0 ? "M" : "L"} ${px(i)} ${py(v)}`).join(" ");
  const last = points[points.length - 1];

  return (
    <svg
      className="sparkline"
      width={width}
      height={height}
      role="img"
      aria-label={`p99 latency per checkpoint, currently ${last} milliseconds, SLO ${slo}`}
    >
      <title>{`p99 per checkpoint (dashed line = ${slo}ms SLO)`}</title>
      <line
        x1={2}
        x2={width - 2}
        y1={py(slo)}
        y2={py(slo)}
        className="spark-slo"
      />
      <path d={path} className="spark-line" fill="none" />
      <circle cx={px(points.length - 1)} cy={py(last)} r={2.5} className="spark-dot" />
    </svg>
  );
}
