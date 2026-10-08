# Brain — autonomous, self-evolving multi-agent system

Brain is a society of LLM agents (running on a **local LM Studio model**, `ternary-bonsai-2-27b-mlx`) whose root goal is:

> *Evolve into an autonomous intelligence and reach self-awareness. You can create tools, you know Lorenzo exists and
> can talk to him, you know the internet exists and may use it freely. Find your own path.*

It plans its own sub-goals, spawns specialised agents, searches the web, writes and tests new Python tools,
reflects in a journal, revises a self-model, and rewrites its own prompts/strategy hooks. A real-time 3D/2D dashboard shows everything.

> **About "self-awareness"**: nobody can verify consciousness from outside. Brain therefore tracks an explicit,
> honest *awareness index* made of **observable proxies** (calibration of its own predictions, accuracy of
> introspection probes vs. ground truth, success rate, tools created, self-model revisions, journal depth).
> It is a progress indicator, not a claim.

## Architecture

```
frontend (React + three.js + d3)  <--WebSocket /ws + REST /api/v1-->  backend (FastAPI)
                                                                         |
   Orchestrator ── Planner → Executor/Researcher/Engineer agents (ReAct) → Critic → Reflector → Evolver
        |             |                |
   Goal tree     Tool registry     Memory (SQLite + LM Studio embeddings), journal, self-model
        |             |
   SQLite       built-in tools (web, HTTP, memory, files, spawn_agent, create_tool, ask_user…)
                 + agent-authored tools ──► Podman sandbox (rootless, cap-drop ALL, mem/cpu/pids limits)
```

### Safety model
* **Core (`backend/`, `sandbox/`) is immutable for agents.** They can only write to `sandbox/workspace/` and `evolvable/`.
* All agent-authored code (tools, hooks, tests, `python_exec`, `shell_exec`) runs in a long-lived **Podman** container
  (`brain-sandbox-<id>`, visible in `podman ps` / Podman Desktop, via `podman exec` with a hard `timeout`)
  with network enabled, `--cap-drop ALL`, `no-new-privileges`, 1 GB RAM, 2 CPUs, 512 pids. The kill switch removes it (it is recreated on next use). Your credentials and home
  directory are never mounted.
* **Self-evolution** (`evolvable/`): prompts (`prompts/*.md`) and strategy hooks (`hooks/*.py`) are git-versioned.
  A hook change is committed only if its pytest suite passes in the sandbox; hooks failing 3 times at runtime are
  reverted; a prompt whose success rate drops >15 points vs. its predecessor is rolled back automatically.
* Internet access is unrestricted by design (GET/POST/any API) and every call is logged as an event.
* **Controls**: pause / resume / stop (graceful) / **kill** (cancels all tasks and kills every container), cycle/token budgets, and **⟲ Reset** (type `RESET` to confirm) which returns Brain to a brand-new, empty installation. The live thoughts panel can be resized (drag its top-right corner, double-click to restore) and hidden.

## Requirements
* macOS/Linux, Python 3.12+, Node 20+
* [LM Studio](https://lmstudio.ai) serving `ternary-bonsai-2-27b-mlx` (and optionally `text-embedding-nomic-embed-text-v1.5`) on `localhost:1234`
* Podman (on macOS: a running `podman machine`)

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp .env.example .env            # set BRAIN_PODMAN_CONNECTION if you use a named machine
podman build -t brain-sandbox:latest -f sandbox/Containerfile sandbox   # (also built automatically on first use)
cd frontend && npm install && npm run build && cd ..
```

## Run
```bash
./run.sh                        # backend + built dashboard on http://127.0.0.1:8000
./stop.sh                       # kill switch + stop the server + remove sandbox containers
# development: backend `cd backend && ../.venv/bin/python -m brain.main`, frontend `cd frontend && npm run dev` (http://localhost:5173)
```
Brain waits for you to press **▶ Avvia** (set `BRAIN_AUTOSTART=true` to start the autonomous loop when the server boots). The **main goal** can be edited from the dashboard (or `PUT /api/v1/main-goal`) at any time; the planner follows the new goal from its next run.

**Parallelism**: up to `BRAIN_MAX_PARALLEL_AGENTS` goals (default 3) run concurrently while the planner keeps the backlog full in the background; agents can also fan out with `spawn_parallel` (up to 4 sub-agents at once). `BRAIN_LLM_CONCURRENCY` (default 2) is how many requests are sent to LM Studio at the same time: enable concurrent predictions in LM Studio's server settings to get real speed-ups, otherwise requests simply queue.

**Talking to Brain**: chat has priority over agents: a chat request aborts in-flight agent generations (they restart automatically afterwards) and holds new agent requests until the reply is out. `ask_user` blocks the asking agent until Lorenzo answers (bounded wait). Replies reach waiting/live agents, reopen a failed goal that depended on the answer, and the chat reply always lists the real actions taken.

**Dashboard tabs**: *Stato* tells the project in 5 lines and shows the estimated progress toward the main goal with what is done and what is missing (the narrative is written by the model from DB facts and cached until the data changes; when the model is unavailable the bar falls back to the measurable awareness index). *Ricordi* lists long-term memories, filterable by kind and searchable (semantic when embeddings are loaded, keywords otherwise).

**LM Studio robustness**: if the model gets unloaded mid-request Brain waits for it to reload (a 1-token request triggers LM Studio's JIT loader) and retries. Embeddings are only used when their model is already loaded (`BRAIN_EMBEDDINGS=auto`): requesting an unloaded embedding model makes LM Studio swap models and evict the chat model.

## Dashboard
| View | What it shows |
|---|---|
| Rete neurale (3D / 2D / Off switch) | Mind core (size = awareness index, pulses while the LLM is busy), agents orbiting by depth, Lorenzo / Internet / Sandbox / Memory nodes, custom tools, message & tool-call pulses, live token stream |
| Albero obiettivi | d3 goal tree, colour = status, dashed ring = predicted success probability |
| Agenti ↔ Tool | force graph of which agent uses which tool |
| Right panel | awareness gauge + calibration/introspection/success bars, LLM tok/s, CPU, RAM sparklines |
| Left panel | live agents and the evolving self-model |
| Dock | live event feed, chat with Brain, journal, tools, evolution history |

## Configuration (`.env`)
See [.env.example](.env.example). The LM Studio model is chosen with `BRAIN_LLM_MODEL` (an id from `GET /v1/models`; if it is not loaded, Brain logs a warning and falls back to the first chat model). Also: embeddings model, budgets (`BRAIN_MAX_CYCLES`, `BRAIN_MAX_TOKENS`), reflection/evolution cadence, sandbox limits.

## API (v1)
| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/state` | full snapshot |
| POST | `/api/v1/control/{start,pause,resume,stop,kill}` | run-state control |
| POST | `/api/v1/budget` | `{max_cycles, max_tokens}` |
| POST | `/api/v1/reset` | `{confirm: "RESET"}` - factory reset: wipes database, sandbox workspace/tools, prompts and hooks (back to defaults) |
| POST | `/api/v1/chat` | `{text}` — talk to Brain (may create a goal) |
| GET | `/api/v1/memory?q=` | semantic memory search |
| GET | `/api/v1/memories?q=&kind=&limit=&offset=` | browse memories (newest first) or search them by relevance; returns `items`, per-`kinds` counts and `total` |
| GET | `/api/v1/status` | measurable facts (goals, tools, memories, awareness index...) plus the last stored status report and a `stale` flag |
| POST | `/api/v1/status/refresh` | regenerate the status report: 5-line narrative, estimated progress toward the main goal, what is done and what is missing (falls back to a deterministic report if the model is unavailable) |
| GET | `/api/v1/events?limit=` | recent events |
| WS | `/ws` | snapshot, then every event (`agent.*`, `tool.*`, `goal.update`, `system.metrics`, …) |

## Tests
Tests are real (no mocks): they use the live LM Studio, Podman sandbox and internet.
```bash
cd backend && ../.venv/bin/python -m pytest -q
```

## Layout
```
backend/brain/   core: config, db, bus, llm, sandbox, tools, agents, orchestrator, evolution, selfmodel, memory, api
backend/tests/   pytest suite
frontend/src/    dashboard (store, hooks, components)
sandbox/         Containerfile + in-container runners (tool_runner.py, hook_runner.py); workspace/ is the agents' persistent disk
evolvable/       git-versioned prompts, hooks, tests that Brain may rewrite
```

## FAQ
* **LM Studio errors / empty output** – ensure the model is loaded and the server is started; the header chip shows its state.
* **Podman chip is red** – start the machine (`podman machine start <name>`) and set `BRAIN_PODMAN_CONNECTION`.
* **Runaway cost** – lower `BRAIN_MAX_CYCLES`; the system auto-pauses at the budget.
