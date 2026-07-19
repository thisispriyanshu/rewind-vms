# Security Policy

## Supported versions

Rewind is pre-1.0; only the latest commit on `master` is supported.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead,
use [GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository ("Security" tab → "Report a vulnerability").

You can expect an acknowledgment within a few days. Once fixed, we'll credit
you in the release notes unless you prefer otherwise.

## Scope notes

- Rewind stores agent state, files, and memory in your database. Treat the
  database credentials with the same care as any production secret; never
  commit `.env`.
- The Idempotent Tool Proxy is a safety layer for *agent* mistakes, not a
  security boundary against malicious tool executors — executors run with the
  host process's privileges.
