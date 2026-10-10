"""Async client for LM Studio's OpenAI-compatible API (chat streaming + embeddings).

Requests carry a priority: chat with the owner (priority 0) preempts agent work (priority 1) so he gets
an answer as soon as possible; preempted agent requests are transparently restarted afterwards.
Among agent requests the deepest sub-agent goes first: its parent is blocked waiting for it, so finishing the
branches that are already open beats starting one more step of everybody (otherwise a spawn_parallel tree starves).
"""
from __future__ import annotations

import asyncio
import heapq
import json
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx

from .bus import EventBus
from .config import Settings
from .jsonutil import extract_json, strip_thinking


# The model reasons in a separate channel that shares the token budget with the answer.
REASONING_HEADROOM = 2500
CHAT_PRIORITY, AGENT_PRIORITY = 0, 1
MAX_RECOVERIES = 4
TRANSIENT = ("unloaded", "shutting down", "failed to load", "empty stream", "unreachable", "engine protocol")


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


class _Preempted(Exception):
    """Internal: this request was aborted to make room for a chat reply and must be restarted."""


class _Gate:
    """Concurrency gate: lower priority number first; while a chat is pending, agent requests wait."""

    def __init__(self, n: int):
        self.n = max(1, n)
        self.in_use = 0
        self.chat_pending = 0
        self._q: list[tuple[int, int, int, asyncio.Future]] = []  # (priority, -depth, arrival, future)
        self._seq = 0

    async def acquire(self, prio: int, depth: int = 0) -> None:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._seq += 1
        heapq.heappush(self._q, (prio, -depth, self._seq, fut))
        self.dispatch()
        try:
            await fut
        except asyncio.CancelledError:
            if fut.done() and not fut.cancelled():  # slot was granted right as we were cancelled
                self.release()
            raise

    def release(self) -> None:
        self.in_use -= 1
        self.dispatch()

    def dispatch(self) -> None:
        while self._q and self.in_use < self.n:
            prio, _, _, fut = self._q[0]
            if prio > CHAT_PRIORITY and self.chat_pending:
                return
            heapq.heappop(self._q)
            if fut.cancelled():
                continue
            self.in_use += 1
            fut.set_result(None)

    def blocked_for(self, prio: int) -> bool:
        return self.in_use >= self.n or (prio > CHAT_PRIORITY and self.chat_pending > 0)

    def ahead(self, prio: int, depth: int = 0) -> int:
        """Requests that run before a new one with this priority and depth: the ones generating plus the ones queued before it."""
        return self.in_use + sum(1 for p, d, _, f in self._q if (p, d) <= (prio, -depth) and not f.cancelled())


@dataclass(eq=False)
class _Call:
    prio: int
    agent: str | None
    runner: asyncio.Task | None = None
    preempted: bool = False


@dataclass
class _Stream:
    parts: list[str] = field(default_factory=list)
    thinking: list[str] = field(default_factory=list)
    n_tokens: int = 0
    first_tok: float | None = None
    finish: str | None = None
    usage_tokens: int = 0


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
        self.preemptions = 0  # agent requests aborted to answer the chat first
        self._rate = _Rate()  # aggregate throughput across all concurrent requests
        self.streams: dict[str, dict] = {}  # agent id -> latest streamed text (for dashboard snapshots)
        self._gate = _Gate(settings.llm_concurrency)
        self._active: set[_Call] = set()
        self._embed_ok: tuple[float, bool] = (0.0, False)
        self._embed_busy = 0
        self._http = httpx.AsyncClient(base_url=settings.llm_url, timeout=httpx.Timeout(600, connect=5))

    async def close(self) -> None:
        await self._http.aclose()

    @property
    def current_tps(self) -> float:
        return self._rate.value(time.time())

    # ---------------------------------------------------------------- slots
    @asynccontextmanager
    async def _slot(self, agent: str | None, prio: int = AGENT_PRIORITY, depth: int = 0):
        """Concurrency slot; tells the UI when a request waits behind others or behind a chat reply."""
        self.queued += 1
        if self._gate.blocked_for(prio):
            if prio > CHAT_PRIORITY and self._gate.chat_pending:
                detail = "paused: priority to the chat"
            else:
                detail = f"queued on LM Studio ({self._gate.ahead(prio, depth)} ahead)"
            await self.bus.publish("agent.state", agent, state="queued", detail=detail)
        try:
            await self._gate.acquire(prio, depth)
        finally:
            self.queued -= 1
        try:
            yield
        finally:
            self._gate.release()

    def _preempt_lower(self) -> None:
        """Abort in-flight agent requests so a chat reply is generated right away."""
        for call in list(self._active):
            if call.prio > CHAT_PRIORITY:
                call.preempted = True
                if call.runner and not call.runner.done():
                    call.runner.cancel()

    # ------------------------------------------------------------- models
    async def list_models(self) -> list[str]:
        r = await self._http.get("/models", timeout=5)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]

    async def model_states(self) -> dict[str, str]:
        """Model id -> 'loaded' | 'not-loaded' (LM Studio's REST API); empty if unavailable."""
        root = self.s.llm_url.rsplit("/v1", 1)[0]
        try:
            r = await self._http.get(f"{root}/api/v0/models", timeout=5)
            r.raise_for_status()
            return {m["id"]: m.get("state", "") for m in r.json().get("data", [])}
        except (httpx.HTTPError, ValueError, KeyError):
            return {}

    async def resolve_model(self) -> str:
        ids = await self.list_models()
        chat = [i for i in ids if "embed" not in i.lower()]
        if self.s.llm_model in ids:
            self.model = self.s.llm_model
        elif chat:
            self.model = chat[0]
            await self.bus.publish(
                "system.log", None, level="warn",
                text=f"Model '{self.s.llm_model}' (BRAIN_LLM_MODEL) is not loaded in LM Studio: using '{self.model}'",
            )
        else:
            raise LLMError("LM Studio exposes no chat model")
        return self.model

    async def health(self) -> dict:
        try:
            states = await self.model_states()
            ids = list(states) if states else await self.list_models()  # one request when the REST API answers
            name = self.model or self.s.llm_model
            return {"ok": True, "model": name, "models": ids, "state": states.get(name)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)[:200]}

    async def ensure_loaded(self) -> bool:
        """Make sure the chat model is loaded: a 1-token request makes LM Studio's JIT loader bring it back."""
        name = self.model or self.s.llm_model
        if (await self.model_states()).get(name) == "loaded":
            return True
        try:
            await self._http.post(
                "/chat/completions", json={"model": name, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1},
                timeout=240,
            )
        except httpx.HTTPError:
            pass
        return (await self.model_states()).get(name) == "loaded"

    async def _recover(self, err: str, agent: str | None, attempt: int) -> None:
        await self.bus.publish(
            "system.log", agent, level="warn",
            text=f"{err} - reloading the model and retrying ({attempt}/{MAX_RECOVERIES})",
        )
        if agent == "voice" and attempt == 1:
            await self.bus.publish("chat.message", None, role="brain", text="LM Studio unloaded the model: I am reloading it, then I will answer you.")
        if not await self.ensure_loaded():
            await asyncio.sleep(min(10 * attempt, 40))

    # --------------------------------------------------------------- chat
    async def chat(self, messages: list[dict], *, priority: int = AGENT_PRIORITY, **kw) -> LLMResult:
        """One completion. Retries transient LM Studio failures; chat priority preempts agent requests."""
        if priority == CHAT_PRIORITY:
            self._gate.chat_pending += 1
            self._preempt_lower()
        try:
            attempt = 0
            while True:
                try:
                    res = await self._chat_once(messages, priority=priority, **kw)
                    if res.completion_tokens == 0:
                        raise LLMError("LM Studio returned an empty stream")
                    return res
                except _Preempted:
                    self.preemptions += 1
                    continue  # waits behind the chat reply, then starts over
                except LLMError as e:
                    if attempt >= MAX_RECOVERIES or not any(k in str(e).lower() for k in TRANSIENT):
                        raise
                    attempt += 1
                    await self._recover(str(e), kw.get("agent"), attempt)
        finally:
            if priority == CHAT_PRIORITY:
                self._gate.chat_pending -= 1
                self._gate.dispatch()

    async def _chat_once(
        self,
        messages: list[dict],
        *,
        priority: int = AGENT_PRIORITY,
        depth: int = 0,
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
        async with self._slot(agent, priority, depth):
            call = _Call(priority, agent)
            self._active.add(call)
            self.busy += 1
            st = _Stream()
            t0 = time.time()
            try:
                await self.bus.publish("llm.start", agent, purpose=purpose)
                await self.bus.publish("agent.state", agent, state="thinking", detail="")
                if call.preempted:
                    raise _Preempted()
                call.runner = asyncio.create_task(self._stream(payload, agent, st))
                try:
                    await call.runner
                except asyncio.CancelledError:
                    if call.preempted:
                        raise _Preempted() from None
                    call.runner.cancel()
                    raise
            finally:
                self._active.discard(call)
                self.busy -= 1
                self.streams.pop(agent or "?", None)
            end = time.time()
        n_tokens = st.n_tokens
        if st.usage_tokens and st.usage_tokens != n_tokens:  # the server's own count is authoritative
            self.total_tokens += st.usage_tokens - n_tokens
            n_tokens = st.usage_tokens
        res = LLMResult("".join(st.parts), n_tokens, end - t0, truncated=st.finish == "length", gen_seconds=(end - st.first_tok) if st.first_tok else 0.0)
        self.last_tps = res.tps
        self.calls += 1
        await self.bus.publish(
            "llm.end", agent, purpose=purpose, tokens=n_tokens, tps=round(res.tps, 1), total_tokens=self.total_tokens,
        )
        return res

    async def _stream(self, payload: dict, agent: str | None, st: _Stream) -> None:
        rate = _Rate()
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
                        st.usage_tokens = int(obj["usage"].get("completion_tokens") or 0)
                    try:
                        choice = obj["choices"][0]
                    except (KeyError, IndexError):
                        continue
                    delta = choice.get("delta", {})
                    st.finish = choice.get("finish_reason") or st.finish
                    piece = delta.get("content") or ""
                    think = delta.get("reasoning_content") or ""
                    now = time.time()
                    if piece or think:
                        st.first_tok = st.first_tok or now
                        st.n_tokens += 1
                        self.total_tokens += 1
                        rate.add(now)
                        self._rate.add(now)
                    if piece:
                        st.parts.append(piece)
                    if think:
                        st.thinking.append(think)
                    if now - last_emit > 0.4:
                        last_emit = now
                        live = "".join(st.parts) or "".join(st.thinking)
                        tps = rate.value(now)
                        self.streams[agent or "?"] = {"text": live[-600:], "reasoning": not st.parts, "tps": tps, "tokens": st.n_tokens}
                        await self.bus.publish(
                            "agent.stream", agent, text=live[-600:], reasoning=not st.parts, tokens=st.n_tokens, tps=tps,
                        )
        except httpx.HTTPError as e:
            raise LLMError(f"LM Studio unreachable: {e}") from e

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
                    {"role": "assistant", "content": strip_thinking(res.text)[:1500] or "(empty reply)"},
                    {"role": "user", "content": (
                        "Invalid reply (no parsable JSON). Reply again with ONLY ONE JSON object: no text before or after, "
                        "no ``` block. Inside strings use \\n for line breaks and \\\" for quotes (never literal line breaks); "
                        "multi-line code goes in a single string with \\n."
                    )},
                ]
        raise LLMError(f"model never produced valid JSON ({last_err})")

    # ---------------------------------------------------------- embeddings
    async def embeddings_available(self) -> bool:
        """Embeddings are only used when their model is already loaded: asking LM Studio for an unloaded
        embedding model makes it swap models, which evicts the chat model mid-generation."""
        mode = self.s.embeddings
        if mode == "off":
            return False
        if mode == "on":
            return True
        ts, ok = self._embed_ok
        if time.time() - ts < 30:
            return ok
        ok = (await self.model_states()).get(self.s.embed_model) == "loaded"
        self._embed_ok = (time.time(), ok)
        return ok

    async def embed(self, texts: list[str], force: bool = False) -> list[list[float]]:
        """Vectors from BRAIN_EMBED_MODEL. `force` skips the loaded-model guard (LM Studio may then load it just-in-time)."""
        if not force and not await self.embeddings_available():
            raise LLMError("embeddings model not loaded: skipped to avoid swapping models in LM Studio")
        await self._embedder_start(len(texts))
        ok = False
        try:
            r = await self._http.post("/embeddings", json={"model": self.s.embed_model, "input": texts}, timeout=120 if force else 60)
            r.raise_for_status()
            out = [d["embedding"] for d in sorted(r.json()["data"], key=lambda d: d["index"])]
            ok = True
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise LLMError(f"embeddings request failed: {str(e)[:200]}") from e
        finally:
            await self._embedder_end(ok, len(texts))
        self._embed_ok = (time.time(), True)
        return out

    async def _embedder_start(self, n: int) -> None:
        """Embedding calls show up as one agent while any is in flight (many small calls would flicker otherwise)."""
        self._embed_busy += 1
        if self._embed_busy == 1:
            await self.bus.publish("agent.spawn", "embedder", role="embedding", parent=None, goal_id=None, task="vectorise text")
        await self.bus.publish("agent.state", "embedder", state="acting", detail="embedding")
        await self.bus.publish("agent.thought", "embedder", action="embed", thought=f"vectorising {n} text(s) with {self.s.embed_model}")

    async def _embedder_end(self, ok: bool, n: int) -> None:
        self._embed_busy = max(0, self._embed_busy - 1)
        if self._embed_busy == 0:
            await self.bus.publish(
                "agent.end", "embedder", success=ok, steps=1,
                summary=f"{n} text(s) vectorised" if ok else "embedding request failed",
            )
