"""Optional model-written answers. Off unless ANSWERER=llm.

Works with any OpenAI-compatible chat endpoint (Azure AI Foundry, Azure OpenAI,
Ollama, etc). The model only phrases the answer. It must return the exact text it
relied on, and app/support.py rejects any quote that is not in the stored passage.
If the call fails or times out we fall back to the extractive answer.
"""
import json
import logging

import httpx

from ..config import settings
from ..retrieval import Hit
from . import Draft, Quote
from .extractive import ExtractiveAnswerer

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You answer questions using only the numbered passages provided.
Reply with JSON only, in this shape:
{"answer": "<one or two sentences>", "quotes": [{"passage": <number>, "text": "<exact words copied from that passage>"}]}
Rules:
- Every quote must be copied character for character from the passage it cites.
- If the passages do not answer the question, reply {"answer": "", "quotes": []}.
- Do not use any knowledge that is not in the passages."""


class LLMAnswerer:
    name = "llm"

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self._transport = transport
        self._fallback = ExtractiveAnswerer()

    def answer(self, question: str, hits: list[Hit]) -> Draft:
        if not hits:
            return Draft(text="", answerer=self.name)
        try:
            return self._ask_model(question, hits)
        except Exception as exc:  # network, timeout, bad JSON: never fail the request
            log.warning("LLM answerer failed, using extractive answer: %s", exc)
            draft = self._fallback.answer(question, hits)
            draft.answerer = "extractive (llm unavailable)"
            return draft

    def _ask_model(self, question: str, hits: list[Hit]) -> Draft:
        if not settings.llm_base_url or not settings.llm_model:
            raise RuntimeError("LLM_BASE_URL and LLM_MODEL are not set")
        passages = "\n".join(f"[{i}] {h.chunk.text}" for i, h in enumerate(hits, start=1))
        body = {
            "model": settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Passages:\n{passages}\n\nQuestion: {question}"},
            ],
        }
        headers = {"Authorization": f"Bearer {settings.llm_api_key}", "api-key": settings.llm_api_key}
        url = settings.llm_base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=settings.llm_timeout_seconds, transport=self._transport) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start:end + 1])
        quotes = []
        for q in data.get("quotes", []):
            number = int(q["passage"])
            if 1 <= number <= len(hits):
                quotes.append(Quote(hits[number - 1].chunk.id, str(q["text"])))
        return Draft(text=str(data.get("answer", "")).strip(), quotes=quotes, answerer=self.name)
