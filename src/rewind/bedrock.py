"""Amazon Bedrock adapters: the LLM and embedder the demo agent plugs in.

Rewind itself is model-agnostic — these are the production-grade defaults.
``TitanEmbedder`` produces 256-dim embeddings matching
the CockroachDB vector index; ``BedrockLLM`` wraps the Converse API for the
incident-response agent.

Requires the ``bedrock`` extra (boto3) and AWS credentials in the environment
(``.env`` is honored via rewind.env.load_dotenv).
"""

from __future__ import annotations

import json
from typing import Any

from rewind.schema import EMBEDDING_DIM

try:
    import boto3
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        'Bedrock support requires boto3: pip install "rewind-agents[bedrock]"'
    ) from exc

DEFAULT_REGION = "us-east-1"
DEFAULT_CLAUDE = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
DEFAULT_TITAN = "amazon.titan-embed-text-v2:0"


class TitanEmbedder:
    """Titan Text Embeddings V2 at the shared EMBEDDING_DIM — a drop-in
    replacement for HashingEmbedder in VectorMemory."""

    def __init__(
        self,
        region: str = DEFAULT_REGION,
        model_id: str = DEFAULT_TITAN,
        dimensions: int = EMBEDDING_DIM,
    ) -> None:
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id
        self.dimensions = dimensions

    def __call__(self, text: str) -> list[float]:
        response = self.client.invoke_model(
            modelId=self.model_id,
            body=json.dumps({"inputText": text, "dimensions": self.dimensions}),
        )
        return json.loads(response["body"].read())["embedding"]


class BedrockLLM:
    """Minimal Converse-API wrapper: one system prompt, one user turn."""

    def __init__(self, region: str = DEFAULT_REGION, model_id: str = DEFAULT_CLAUDE) -> None:
        self.client = boto3.client("bedrock-runtime", region_name=region)
        self.model_id = model_id

    def complete(self, prompt: str, system: str | None = None, max_tokens: int = 1024) -> str:
        kwargs: dict[str, Any] = {
            "modelId": self.model_id,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {"maxTokens": max_tokens},
        }
        if system:
            kwargs["system"] = [{"text": system}]
        response = self.client.converse(**kwargs)
        return response["output"]["message"]["content"][0]["text"]

    def complete_json(self, prompt: str, system: str | None = None) -> dict[str, Any]:
        """Ask for a JSON object and parse it (tolerates code fences)."""
        text = self.complete(prompt, system=system).strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            text = text[4:] if text.startswith("json") else text
        return json.loads(text)
