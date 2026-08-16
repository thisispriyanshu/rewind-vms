import React from "react";

const STATUS_META = {
  staged: { label: "HELD — not sent", cls: "held" },
  discarded: { label: "DISCARDED — never sent", cls: "discarded" },
  committed: { label: "DELIVERED", cls: "delivered" },
};

function clock(iso) {
  return new Date(iso).toLocaleTimeString([], { hour12: false });
}

export default function SlackPanel({ tree }) {
  const messages = (tree?.effects || [])
    .filter((e) => e.tool_name === "slack.post")
    .sort((a, b) => (a.created_at < b.created_at ? -1 : 1));

  const held = messages.filter((m) => m.status === "staged").length;

  return (
    <div className="panel slack-panel">
      <div className="panel-caption">
        <div>
          <span className="caption-title">#incident-bridge</span>
          <span className="caption-sub">
            outbound Slack, via the staging proxy
            {held > 0 ? ` — ${held} held` : ""}
          </span>
        </div>
      </div>
      <div className="slack-scroll">
        {messages.length === 0 && (
          <div className="hint-text pad">
            When the agent posts to Slack, the message lands here first — staged, not sent.
          </div>
        )}
        {messages.map((m) => {
          const meta = STATUS_META[m.status] || STATUS_META.staged;
          return (
            <div key={m.id} className={`slack-msg ${meta.cls}`}>
              <div className="slack-avatar">SA</div>
              <div className="slack-body">
                <div className="slack-meta">
                  <span className="slack-author">sre-agent</span>
                  <span className="slack-time mono">{clock(m.created_at)}</span>
                  <span className={`slack-status ${meta.cls}`}>{meta.label}</span>
                </div>
                <div className="slack-text">{m.payload?.text}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="explainer">
        A sent message can't be unsent — so the agent never posts directly. Staged messages fire
        only when their checkpoint survives; a rewind discards them before Slack ever sees them.
      </div>
    </div>
  );
}
