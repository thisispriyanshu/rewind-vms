"""Small helpers for wiring a CockroachDB Managed MCP Server connection.

This module provides a convenience to take the JSON/snippet copied from
the CockroachDB Cloud Console and write a `.env` entry `REWIND_DATABASE_URL`
so the rest of the project can pick it up. It is intentionally light-weight —
the operator still needs to copy the connection string from the cloud UI.
"""
from __future__ import annotations

import os
import json
from pathlib import Path


def configure_from_snippet(snippet: str, env_path: str | None = None) -> str:
    """Accept a connection snippet (JSON or raw URL) and write a .env file entry.

    Returns the connection URL that was written. This helper is a convenience
    for demo reviewers who paste the MCP connection snippet into the terminal.
    """
    url = snippet.strip()
    # Try to parse JSON in case the console returned a JSON block.
    try:
        parsed = json.loads(snippet)
        # common key names: "connectionString", "jdbcUrl", "uri"
        for key in ("connectionString", "jdbcUrl", "uri", "url"):
            if key in parsed:
                url = parsed[key]
                break
    except Exception:
        pass

    if not env_path:
        env_path = Path.cwd() / ".env"
    else:
        env_path = Path(env_path)

    line = f"REWIND_DATABASE_URL={url}\n"
    # Append or create
    if env_path.exists():
        with env_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
    else:
        with env_path.open("w", encoding="utf-8") as fh:
            fh.write(line)
    return url


def example_usage() -> None:
    print("Copy the CockroachDB MCP connection snippet and paste it into this script.")
    print("Then run: python -c \"from rewind.mcp import configure_from_snippet; configure_from_snippet(<paste>)\"")
