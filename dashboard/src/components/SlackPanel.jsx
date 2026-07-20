import React from "react";

const STATUS_META = {
  staged: { label: "⏸ HELD by Rewind — not sent", cls: "held" },
  discarded: { label: "🗑 DISCARDED — never sent", cls: "discarded" },
  committed: { label: "✓ DELIVERED", cls: "delivered" },
};

export default function SlackPanel({ tree }) {
  const messages = (tree?.effects || [])
    .filter((e) => e.tool_name === "slack.post")
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1));

  const held = messages.filter((m) => m.status === "staged").length;

  return (
    <div className="panel slack-panel">
      <div className="panel-caption">
        <span className="caption-title"># incident-bridge</span>
        <span className="caption-sub">
          {held > 0 ? `${held} message${held === 1 ? "" : "s"} held in staging` : "external side effects"}
        </span>
      </div>
      <div className="slack-scroll">
        {messages.length === 0 && (
          <div className="hint-text pad">
            When the agent tries to post to Slack, the message lands here — staged, not sent.
          </div>
        )}
        {messages.map((m) => {
          const meta = STATUS_META[m.status] || STATUS_META.staged;
          return (
            <div key={m.id} className={`slack-msg ${meta.cls}`}>
              <div className="slack-avatar">🤖</div>
              <div className="slack-body">
                <div className="slack-meta">
                  <span className="slack-author">sre-agent</span>
                  <span className={`slack-status ${meta.cls}`}>{meta.label}</span>
                </div>
                <div className="slack-text">{m.payload?.text}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="explainer">
        You can't un-send a Slack message — so the agent never sends directly. Staged messages
        only fire when their checkpoint survives; a rewind discards them before the real world
        ever sees them.
      </div>
    </div>
  );
}
