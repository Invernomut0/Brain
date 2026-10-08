"""Async client for LM Studio's OpenAI-compatible API (chat streaming + embeddings)."""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import httpx

from .bus import EventBus
from .config import Settings
from .jsonutil import extract_json, strip_thinking


# The model reasons in a separate channel that shares the token budget with the answer.
REASONING_HEADROOM = 2500


@dataclass
class LLMResult:
    text: str
    completion_tokens: int
    elapsed: float
    truncated: bool = False

    @property
    def tps(self) -> float:
        return self.completion_tokens / self.elapsed if self.elapsed > 0 else 0.0


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, bus: EventBus):
        self.s = settings
        self.bus = bus
        self.model: str | None = None
        self.total_tokens = 0
        self.last_tps = 0.0
        self.calls = 0
        self.busy = 0
        self._sem = asyncio.Semaphore(settings.llm_concurrency)
        self._http = httpx.AsyncClient(base_url=settings.llm_url, timeout=httpx.Timeout(600, connect=5))

    async def close(self) -> None:
        await self._http.aclose()

    async def list_models(self) -> list[str]:
        r = await self._http.get("/models", timeout=5)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    async def resolve_model(self) -> str:
        ids = await self.list_models()
        chat = [i for i in ids if "embed" not in i.lower()]
        if self.s.llm_model in ids:
            self.model = self.s.llm_model
        elif chat:
            self.model = chat[0]
            await self.bus.publish(
                "system.log", None, level="warn",
                text=f"Modello '{self.s.llm_model}' (BRAIN_LLM_MODEL) non caricato in LM Studio: uso '{self.model}'",
            )
        else:
            raise LLMError("LM Studio exposes no chat model")
        return self.model

    async def health(self) -> dict:
        try:
            ids = await self.list_models()
            return {"ok": True, "model": self.model or self.s.llm_model, "models": ids}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}

    async def chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1500,
        agent: str | None = None,
        purpose: str = "",
    ) -> LLMResult:
        """Stream a completion; emits throttled `agent.stream` events for the live UI."""
        if not self.model:
            await self.resolve_model()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens + REASONING_HEADROOM,
            "stream": True,
        }
        async with self._sem:
            self.busy += 1
            await self.bus.publish("llm.start", agent, purpose=purpose)
            t0 = time.time()
            parts: list[str] = []
            thinking: list[str] = []
            finish = None
            n_tokens = 0
            last_emit = 0.0
            try:
                async with self._http.stream("POST", "/chat/completions", json=payload) as r:
                    if r.status_code >= 400:
                        body = (await r.aread()).decode(errors="replace")[:300]
                        raise LLMError(f"LM Studio HTTP {r.status_code}: {body}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            choice = json.loads(chunk)["choices"][0]
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                        delta = choice.get("delta", {})
                        finish = choice.get("finish_reason") or finish
                        piece = delta.get("content") or ""
                        think = delta.get("reasoning_content") or ""
                        if piece or think:
                            n_tokens += 1
                        if piece:
                            parts.append(piece)
                        if think:
                            thinking.append(think)
                        now = time.time()
                        if now - last_emit > 0.4:
                            last_emit = now
                            live = "".join(parts) or "".join(thinking)
                            await self.bus.publish(
                                "agent.stream", agent, text=live[-600:], reasoning=not parts,
                                tokens=n_tokens, tps=n_tokens / max(now - t0, 1e-3),
                            )
            except httpx.HTTPError as e:
                raise LLMError(f"LM Studio unreachable: {e}") from e
            finally:
                self.busy -= 1
            elapsed = time.time() - t0
        res = LLMResult("".join(parts), n_tokens, elapsed, truncated=finish == "length")
        self.total_tokens += n_tokens
        self.last_tps = res.tps
        self.calls += 1
        await self.bus.publish(
            "llm.end", agent, purpose=purpose, tokens=n_tokens, tps=round(res.tps, 1),
            total_tokens=self.total_tokens,
        )
        return res

    async def chat_json(
        self,
        messages: list[dict],
        *,
        retries: int = 2,
        **kw,
    ) -> dict:
        """Chat and parse a JSON object, asking the model to repair malformed output."""
        msgs = list(messages)
        last_err = ""
        for _ in range(retries + 1):
            res = await self.chat(msgs, **kw)
            try:
                return extract_json(res.text)
            except ValueError as e:
                last_err = str(e)
                if res.truncated:  # ran out of budget while reasoning: retry with more room, same prompt
                    kw["max_tokens"] = min(int(kw.get("max_tokens", 1500) * 1.6), 6000)
                    continue
                msgs = msgs + [
                    {"role": "assistant", "content": strip_thinking(res.text)[:1500] or "(risposta vuota)"},
                    {"role": "user", "content": (
                        "Risposta non valida (nessun JSON parsabile). Rispondi di nuovo SOLO con UN oggetto JSON: nessun testo prima o dopo, "
                        "nessun blocco ```. Dentro le stringhe usa \\n per andare a capo e \\\" per le virgolette (mai a capo letterali); "
                        "il codice multilinea va in una stringa con \\n."
                    )},
                ]
        raise LLMError(f"model never produced valid JSON ({last_err})")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        r = await self._http.post("/embeddings", json={"model": self.s.embed_model, "input": texts}, timeout=60)
        r.raise_for_status()
        return [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
