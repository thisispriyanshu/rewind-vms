import React from "react";

function Val({ v }) {
  if (v === null || v === undefined) return <span className="val missing">—</span>;
  return <span className="val">{typeof v === "string" ? v : JSON.stringify(v)}</span>;
}

export default function DiffPanel({ selected, headState, selectedState, diff, onRewind, canRewind }) {
  if (!selected) {
    return <div className="panel hint-text">Click a checkpoint node to inspect it.</div>;
  }
  return (
    <div className="panel">
      <div className="panel-head">
        <div>
          <div className="panel-title">
            Step {selected.step_index}
            {selected.label ? ` — ${selected.label}` : ""}
          </div>
          <div className="panel-sub">{new Date(selected.created_at).toLocaleTimeString()}</div>
        </div>
        {canRewind && (
          <button className="rewind-btn" onClick={onRewind}>
            ⏪ Rewind &amp; branch here
          </button>
        )}
      </div>

      <div className="cols">
        <div>
          <div className="col-title">State at this checkpoint</div>
          <table className="kv">
            <tbody>
              {Object.entries(selectedState || {}).map(([k, v]) => (
                <tr key={k}>
                  <td className="key">{k}</td>
                  <td>
                    <Val v={v} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <div className="col-title">Diff vs. current head</div>
          {diff && Object.keys(diff).length > 0 ? (
            <table className="kv">
              <tbody>
                {Object.entries(diff).map(([k, { a, b }]) => (
                  <tr key={k}>
                    <td className="key">{k}</td>
                    <td>
                      <Val v={b} /> <span className="arrow">→</span> <Val v={a} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="hint-text">No differences.</div>
          )}
        </div>
      </div>
    </div>
  );
}
