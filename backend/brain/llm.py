"""Async client for LM Studio's OpenAI-compatible API (chat streaming + embeddings)."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from contextlib import asynccontextmanager
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
    gen_seconds: float = 0.0  # time spent generating, excluding queueing / prompt processing

    @property
    def tps(self) -> float:
        secs = self.gen_seconds or self.elapsed
        return self.completion_tokens / secs if secs > 0 else 0.0


class _Rate:
    """Tokens/s over a short sliding window; time spent waiting for the first token does not count."""

    def __init__(self, window: float = 3.0):
        self.window = window
        self.ts: deque[float] = deque()

    def add(self, now: float) -> None:
        self.ts.append(now)
        self._prune(now)

    def _prune(self, now: float) -> None:
        while self.ts and now - self.ts[0] > self.window:
            self.ts.popleft()

    def value(self, now: float) -> float:
        self._prune(now)
        if len(self.ts) < 2:
            return 0.0
        span = self.ts[-1] - self.ts[0]
        return (len(self.ts) - 1) / span if span > 0.05 else 0.0


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
        self.queued = 0
        self._rate = _Rate()  # aggregate throughput across all concurrent requests
        self.streams: dict[str, dict] = {}  # agent id -> latest streamed text (for dashboard snapshots)
        self._call_seq = 0
        self._sem = asyncio.Semaphore(settings.llm_concurrency)
        self._http = httpx.AsyncClient(base_url=settings.llm_url, timeout=httpx.Timeout(600, connect=5))

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def current_tps(self) -> float:
        return self._rate.value(time.time())

    @asynccontextmanager
    async def _slot(self, agent: str | None):
        """Concurrency slot; tells the UI when a request is queued behind others."""
        self.queued += 1
        if self._sem.locked():
            await self.bus.publish("agent.state", agent, state="queued", detail="in coda su LM Studio")
        try:
            await self._sem.acquire()
        finally:
            self.queued -= 1
        try:
            yield
        finally:
            self._sem.release()

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

    async def chat(self, messages: list[dict], **kw) -> LLMResult:
        """One completion, retrying transient LM Studio failures (model unloaded / reloading, empty stream)."""
        for attempt in range(3):
            try:
                res = await self._chat_once(messages, **kw)
                if res.completion_tokens == 0:
                    raise LLMError("LM Studio returned an empty stream")
                return res
            except LLMError as e:
                transient = any(k in str(e).lower() for k in ("unloaded", "shutting down", "failed to load", "empty stream", "unreachable"))
                if attempt == 2 or not transient:
                    raise
                await self.bus.publish("system.log", kw.get("agent"), level="warn", text=f"{e} - nuovo tentativo tra 8s ({attempt + 1}/2)")
                await asyncio.sleep(8)
        raise LLMError("unreachable")  # pragma: no cover

    async def _chat_once(
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
        async with self._slot(agent):
            self.busy += 1
            self._call_seq += 1
            await self.bus.publish("llm.start", agent, purpose=purpose)
            await self.bus.publish("agent.state", agent, state="thinking", detail="")
            t0 = time.time()
            first_tok: float | None = None
            rate = _Rate()
            usage_tokens = 0
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
                            obj = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("error"):  # LM Studio reports engine failures as an in-band error event
                            raise LLMError(f"LM Studio: {str(obj['error'].get('message', obj['error']))[:200]}")
                        if obj.get("usage"):
                            usage_tokens = int(obj["usage"].get("completion_tokens") or 0)
                        try:
                            choice = obj["choices"][0]
                        except (KeyError, IndexError):
                            continue
                        delta = choice.get("delta", {})
                        finish = choice.get("finish_reason") or finish
                        piece = delta.get("content") or ""
                        think = delta.get("reasoning_content") or ""
                        now = time.time()
                        if piece or think:
                            first_tok = first_tok or now
                            n_tokens += 1
                            self.total_tokens += 1
                            rate.add(now)
                            self._rate.add(now)
                        if piece:
                            parts.append(piece)
                        if think:
                            thinking.append(think)
                        if now - last_emit > 0.4:
                            last_emit = now
                            live = "".join(parts) or "".join(thinking)
                            tps = rate.value(now)
                            self.streams[agent or "?"] = {"text": live[-600:], "reasoning": not parts, "tps": tps, "tokens": n_tokens}
                            await self.bus.publish(
                                "agent.stream", agent, text=live[-600:], reasoning=not parts, tokens=n_tokens, tps=tps,
                            )
            except httpx.HTTPError as e:
                raise LLMError(f"LM Studio unreachable: {e}") from e
            finally:
                self.busy -= 1
                self.streams.pop(agent or "?", None)
            end = time.time()
            elapsed = end - t0
        if usage_tokens and usage_tokens != n_tokens:  # the server's own count is authoritative
            self.total_tokens += usage_tokens - n_tokens
            n_tokens = usage_tokens
        res = LLMResult("".join(parts), n_tokens, elapsed, truncated=finish == "length", gen_seconds=(end - first_tok) if first_tok else 0.0)
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
