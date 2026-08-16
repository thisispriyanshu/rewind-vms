import React, { useEffect, useState } from "react";
import { api } from "../api.js";

const TABS = ["State", "Diff", "Files", "Memory", "Integrations"];

function Val({ v }) {
  if (v === null || v === undefined) return <span className="val missing">—</span>;
  return <span className="val">{typeof v === "string" ? v : JSON.stringify(v)}</span>;
}

export default function Inspector({ tree, selected, headId, onRewind }) {
  const [tab, setTab] = useState("State");
  const [state, setState] = useState(null);
  const [diff, setDiff] = useState(null);
  const [files, setFiles] = useState([]);
  const [memories, setMemories] = useState([]);

  const selectedId = selected?.id;
  useEffect(() => {
    if (!selectedId) return;
    let live = true;
    (async () => {
      try {
        const [s, f, m] = await Promise.all([
          api.state(selectedId),
          api.files(selectedId),
          api.memories(selectedId),
        ]);
        if (!live) return;
        setState(s);
        setFiles(f);
        setMemories(m);
        setDiff(selectedId === headId ? {} : await api.diff(selectedId, headId));
      } catch {
        /* stale selection during poll */
      }
    })();
    return () => {
      live = false;
    };
  }, [selectedId, headId]);

  if (!selected) {
    return (
      <div className="panel inspector">
        <div className="panel-caption">
          <div>
            <span className="caption-title">Checkpoint Inspector</span>
          </div>
        </div>
        <div className="hint-text pad">
          Select a checkpoint in the tree or feed to inspect the agent's exact working memory, virtual file system, lineaged vector index, and staged tool proxy side-effects.
        </div>
      </div>
    );
  }

  const canRewind = selectedId && selectedId !== headId;

  return (
    <div className="panel inspector">
      <div className="panel-caption">
        <div>
          <span className="caption-title">
            <span className="sha mono">{selected.id.slice(0, 8)}</span>
            {selected.label ? ` ${selected.label}` : ""}
          </span>
          <span className="caption-sub">
            step {selected.step_index} ·{" "}
            {new Date(selected.created_at).toLocaleTimeString([], { hour12: false })}
            {selectedId === headId ? " · HEAD" : ""}
          </span>
        </div>
        {canRewind && (
          <button className="rewind-btn" onClick={onRewind}>
            Rewind Here…
          </button>
        )}
      </div>

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
            {t === "Memory" && memories.length > 0 ? ` (${memories.length})` : ""}
          </button>
        ))}
      </div>

      <div className="inspector-body">
        {tab === "State" && (
          <table className="kv">
            <tbody>
              {Object.entries(state || {})
                .filter(([k]) => k !== "thought")
                .map(([k, v]) => (
                  <tr key={k}>
                    <td className="key">{k}</td>
                    <td>
                      <Val v={v} />
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        )}

        {tab === "Diff" &&
          (diff && Object.keys(diff).length > 0 ? (
            <>
              <div className="diff-note">this checkpoint ⟷ current head</div>
              <table className="kv">
                <tbody>
                  {Object.entries(diff)
                    .filter(([k]) => k !== "thought")
                    .map(([k, { a, b }]) => (
                      <tr key={k}>
                        <td className="key">{k}</td>
                        <td>
                          <span className="diff-old">
                            <Val v={b} />
                          </span>
                          <span className="arrow"> → </span>
                          <span className="diff-new">
                            <Val v={a} />
                          </span>
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </>
          ) : (
            <div className="hint-text">No differences vs. the current head.</div>
          ))}

        {tab === "Files" &&
          (files.length > 0 ? (
            files.map((f) => (
              <div key={f.path} className="vfs-file">
                <div className="vfs-path mono">
                  {f.path} <span className="caption-sub">{f.size} B</span>
                </div>
                <pre className="vfs-content">{f.text ?? "(binary)"}</pre>
              </div>
            ))
          ) : (
            <div className="hint-text">No files existed at this checkpoint.</div>
          ))}

        {tab === "Memory" &&
          (memories.length > 0 ? (
            <>
              <div className="diff-note">
                semantic memories reachable from this checkpoint — vector-indexed in CockroachDB
              </div>
              {memories.map((m) => (
                <div key={m.id} className="memory-row">
                  <span className="sha mono">{m.checkpoint_id.slice(0, 8)}</span> {m.content}
                </div>
              ))}
            </>
          ) : (
            <div className="hint-text">The agent had formed no memories yet.</div>
          ))}

        {tab === "Integrations" && (
          <div className="integrations-tab">
            <div className="integration-box">
              <div className="box-title">🪳 CockroachDB Distributed Persistence</div>
              <div className="box-detail">
                • <b>Table:</b> <code>checkpoints</code> &amp; <code>state_kv</code>
                <br />
                • <b>Vector Index:</b> <code>agent_memory (VECTOR(256))</code>
                <br />
                • <b>Time-Travel Read:</b> <code>AS OF SYSTEM TIME '{selected.created_at}'</code>
                <br />
                • <b>Transaction:</b> Serializable commit lock across state deltas &amp; VFS.
              </div>
            </div>

            <div className="integration-box">
              <div className="box-title">☁️ AWS Cloud Stack</div>
              <div className="box-detail">
                • <b>Bedrock LLM:</b> Claude 3.5 Sonnet (Converse API)
                <br />
                • <b>Bedrock Embeddings:</b> Amazon Titan Text V2 (256-dim)
                <br />
                • <b>Execution Engine:</b> AWS Lambda Public Function URL
                <br />
                • <b>Artifact VFS Storage:</b> Amazon S3 Blob Backend
              </div>
            </div>

            <div className="integration-box">
              <div className="box-title">🛡️ Idempotent Tool Proxy</div>
              <div className="box-detail">
                • <b>Staging Mode:</b> <code>staged</code> (Sandbox holds side-effects)
                <br />
                • <b>Idempotency Key:</b> Stable hash prevents double-firing
                <br />
                • <b>Rewind Guard:</b> Unflushed side-effects auto-purged on rewind
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
