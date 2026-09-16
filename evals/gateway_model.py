"""DeepEval custom model that routes through OUR inference gateway.

DeepEval's metrics need an LLM to judge/generate with, and default to calling
OpenAI directly. This app's rule is that every inference call goes through the
internal gateway (see app/services/helpdesk_draft_service.py,
helpdesk_classifier.py, voice_kb_ingest.py) — never a vendor SDK. This wraps
the same /chat endpoint so evaluation follows that same rule, and needs no
OpenAI key.

`generate` intentionally takes no **kwargs: DeepEval's `generate_with_schema`
tries calling generate(prompt, schema=...) first and falls back to plain-text
generation on a TypeError. Accepting **kwargs here would silently swallow the
schema argument and break that fallback.
"""

import httpx

from deepeval.models.base_model import DeepEvalBaseLLM

from app.core.config import get_settings


class GatewayModel(DeepEvalBaseLLM):
    def load_model(self):
        return self

    def generate(self, prompt: str) -> str:
        settings = get_settings()
        resp = httpx.post(
            f"{settings.inference_base_url}/chat",
            headers={"Authorization": f"Bearer {settings.inference_api_key}"},
            json={"messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["output"]

    async def a_generate(self, prompt: str) -> str:
        # No real async path to the gateway exists elsewhere in this codebase;
        # DeepEval only needs an awaitable, not true concurrency, to run its
        # judge calls.
        return self.generate(prompt)

    def get_model_name(self) -> str:
        return "candidate-experience-gateway"
