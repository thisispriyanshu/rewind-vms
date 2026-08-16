import React from "react";

export default function RewindModal({ plan, onCancel, onConfirm, busy }) {
  if (!plan) return null;
  const { target, abandonedSteps, discardCount } = plan;
  return (
    <div className="modal-backdrop" onClick={onCancel}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <span className="modal-icon">⏪</span>
          <div className="modal-title">Rewind Agent State &amp; Memory</div>
        </div>

        <div className="modal-target">
          <span className="sha mono">{target.id.slice(0, 8)}</span>
          <span className="target-label">
            Step {target.step_index} — <b>{target.label}</b>
          </span>
        </div>

        <div className="modal-impact-grid">
          <div className="impact-card warning">
            <span className="impact-icon">🌿</span>
            <div>
              <b>Non-Destructive Branching</b>
              <p>The current timeline is abandoned but preserved in CockroachDB for audit. {abandonedSteps} poisoned step{abandonedSteps === 1 ? "" : "s"} will be isolated.</p>
            </div>
          </div>

          <div className={`impact-card ${discardCount > 0 ? "danger" : "neutral"}`}>
            <span className="impact-icon">🛡️</span>
            <div>
              <b>Idempotent Tool Protection</b>
              <p>
                {discardCount > 0 ? (
                  <span>
                    <b className="text-red">{discardCount} Staged Side-Effect{discardCount === 1 ? "" : "s"} (Slack Alerts)</b> will be permanently <b>discarded</b> — they were safely caught in the sandbox and will NEVER hit Slack.
                  </span>
                ) : (
                  "No staged side-effects will be discarded."
                )}
              </p>
            </div>
          </div>

          <div className="impact-card info">
            <span className="impact-icon">🧠</span>
            <div>
              <b>Lineaged Memory Reset</b>
              <p>The agent's vector memory index in CockroachDB rolls back to step {target.step_index}. Poisoned conclusions are purged from future recall.</p>
            </div>
          </div>
        </div>

        <div className="modal-actions">
          <button className="ghost-btn" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="rewind-btn highlight" onClick={onConfirm} disabled={busy}>
            {busy ? "Executing Rewind…" : `⏪ Rewind & Branch from ${target.id.slice(0, 8)}`}
          </button>
        </div>
      </div>
    </div>
  );
}
