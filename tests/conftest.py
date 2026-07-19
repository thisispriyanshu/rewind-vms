"""Shared test config: load .env so integration tests find the cluster."""

from pathlib import Path

from rewind.env import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
