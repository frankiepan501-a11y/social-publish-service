from __future__ import annotations

import httpx


class AiGenerationError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 45):
        if not api_key:
            raise AiGenerationError("GENERATION_AI_API_KEY is not configured")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def chat_json(self, *, system: str, user: str) -> str:
        url = f"{self.base_url}/chat/completions"
        payload = {
            "model": self.model,
            "temperature": 0.5,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
        if response.status_code >= 400:
            raise AiGenerationError(f"AI provider returned HTTP {response.status_code}")
        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiGenerationError("AI provider response missing message content") from exc

