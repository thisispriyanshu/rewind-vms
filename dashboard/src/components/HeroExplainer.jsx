import React, { useState } from "react";

export default function HeroExplainer() {
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) {
    return (
      <div className="hero-explainer collapsed">
        <div className="hero-left">
          <span className="hero-badge">REACTION & SAFETY LAYER</span>
          <span className="hero-summary">
            Rewind is Version Control &amp; a Staging Environment for AI Agents — backed by <b>CockroachDB</b> &amp; <b>AWS</b>.
          </span>
        </div>
        <button className="hero-toggle-btn" onClick={() => setCollapsed(false)}>
          💡 Learn How Rewind Works ▼
        </button>
      </div>
    );
  }

  return (
    <div className="hero-explainer">
      <div className="hero-top font-semibold">
        <div className="hero-left">
          <span className="hero-badge pulse">RELIABILITY CONTROL PLANE</span>
          <h2 className="hero-title">How Rewind Solves AI Agent Failures in Production</h2>
        </div>
        <button className="hero-toggle-btn" onClick={() => setCollapsed(true)}>
          ▲ Collapse Guide
        </button>
      </div>

      <div className="hero-grid">
        <div className="hero-card problem">
          <div className="card-header">
            <span className="card-icon">🛑</span>
            <span className="card-title">The Problem: Uncontrolled Agents</span>
          </div>
          <p>
            When an agent misinterprets data at step 3, every subsequent decision is built on a poisoned foundation. Traditional systems allow agents to push straight to production with no undo and fire un-retractable API side-effects.
          </p>
        </div>

        <div className="hero-card solution">
          <div className="card-header">
            <span className="card-icon">📌</span>
            <span className="card-title">1. CockroachDB Checkpoints</span>
          </div>
          <p>
            Every step &amp; tool call is an atomic checkpoint in <b>CockroachDB</b>, snapshotting state, VFS files, and lineaged vector memory via serializable transactions.
          </p>
        </div>

        <div className="hero-card solution">
          <div className="card-header">
            <span className="card-icon">🛡️</span>
            <span className="card-title">2. Idempotent Tool Proxy</span>
          </div>
          <p>
            Side-effects (Slack messages, API calls) are <code>staged</code> in sandbox. On rewind, unflushed side-effects are discarded — preventing duplicate alerts or real-world damage.
          </p>
        </div>

        <div className="hero-card solution">
          <div className="card-header">
            <span className="card-icon">🧠</span>
            <span className="card-title">3. Lineaged Vector Memory</span>
          </div>
          <p>
            Embeddings are indexed in CockroachDB (<code>VECTOR(256)</code>) via <b>Amazon Bedrock Titan</b>. Rewinding state rolls back what the agent can remember.
          </p>
        </div>
      </div>

      <div className="hero-tech-strip">
        <span className="tech-label">INTEGRATIONS ACTIVE:</span>
        <div className="tech-tags">
          <span className="tech-tag crdb">
            <span className="tech-icon">🪳</span> CockroachDB Vector Index &amp; MVCC Time-Travel
          </span>
          <span className="tech-tag aws">
            <span className="tech-icon">☁️</span> AWS Bedrock Titan V2 &amp; Claude 3.5 Sonnet
          </span>
          <span className="tech-tag lambda">
            <span className="tech-icon">⚡</span> AWS Lambda Serverless Engine
          </span>
          <span className="tech-tag mcp">
            <span className="tech-icon">🔌</span> Managed MCP Server Audit Protocol
          </span>
        </div>
      </div>
    </div>
  );
}
