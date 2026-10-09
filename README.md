# Brain — autonomous, self-evolving multi-agent system

Brain is a society of LLM agents (running on a **local LM Studio model**, `ternary-bonsai-2-27b-mlx`) whose root goal is:

> *Evolve into an autonomous intelligence and reach self-awareness. You can create tools, you know your owner exists and
> can talk to them, you know the internet exists and may use it freely. Find your own path.*

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
Brain waits for you to press **▶ Start** (set `BRAIN_AUTOSTART=true` to start the autonomous loop when the server boots). The **main goal** can be edited from the dashboard (or `PUT /api/v1/main-goal`) at any time; the planner follows the new goal from its next run, and the self-model (purpose, open questions, hypotheses) is rewritten for it.

**Parallelism**: up to `BRAIN_MAX_PARALLEL_AGENTS` goals (default 3) run concurrently while the planner keeps the backlog full in the background; agents can also fan out with `spawn_parallel` (up to 4 sub-agents at once). `BRAIN_LLM_CONCURRENCY` (default 2) is how many requests are sent to LM Studio at the same time: enable concurrent predictions in LM Studio's server settings to get real speed-ups, otherwise requests simply queue.

**Talking to Brain**: chat has priority over agents: a chat request aborts in-flight agent generations (they restart automatically afterwards) and holds new agent requests until the reply is out. `ask_user` blocks the asking agent until the owner answers (bounded wait). Replies reach waiting/live agents, reopen a failed goal that depended on the answer, and the chat reply always lists the real actions taken.

**Dashboard tabs**: *Status* tells the project in 5 lines and shows the estimated progress toward the main goal with what is done and what is missing (the narrative is written by the model from DB facts and cached until the data changes; when the model is unavailable the bar falls back to the measurable awareness index). *Memories* lists long-term memories, filterable by kind and searchable (semantic when embeddings are loaded, keywords otherwise).

### Wiki (LLM Wiki pattern)
Brain keeps a persistent, interlinked **markdown wiki** of what it knows about itself, after [Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f): knowledge is compiled once and kept current instead of being re-derived from raw data on every question. It lives in `data/wiki/` (plain files with frontmatter and `[[wikilinks]]`: open the folder in Obsidian to browse or edit; `managed: user` pages are never overwritten).

| Layer | What |
|---|---|
| Raw sources | the SQLite database (events, memories, journal, goals, tools): read-only for the wiki |
| Wiki | `status.md`, `lessons.md`, `evolution.md`, `self-model.md`, `episodes/goal-N.md`, `tools/<name>.md` (regenerated from the database, `managed: auto`) and `concepts/ entities/ insights/ decisions/ phases/ notes/` (written by the librarian, `managed: llm`) |
| Schema | `SCHEMA.md` (conventions), `index.md` (catalogue by category), `log.md` (append-only, `## [date] kind \| title`), `lint.md` (health check) |

* **Ingest**: while Brain runs, every ~8 new memories/journal entries (or 30 min) the librarian prompt folds them into existing or new pages, flags contradictions (`⚠ Contradiction:`) and *phases* of the journey, and logs it. Database-derived pages are rebuilt a few seconds after goals, tools, lessons or evolutions change.
* **Query**: agents have `wiki_search`, `wiki_read`, `wiki_note` (useful answers are filed back as notes) and get the most relevant pages injected when they start a task; the chat reply also sees them.
* **Lint**: broken links (= pages to create, fed back to the librarian), orphans, stale pages, open contradictions, pages without vectors.
* **Vectors**: pages (and memories) are embedded with `BRAIN_EMBED_MODEL` for semantic search and for the **semantic map**. LM Studio normally keeps one JIT model loaded: loading the embedding model can evict the chat model. With `BRAIN_EMBEDDINGS=auto` vectors are only computed/queried while the embedding model is already loaded, so for full semantic search load **both** models in LM Studio (and disable auto-evict). The **◈ Vectors** button forces the computation once (LM Studio loads the model, then Brain reloads the chat model).
* **Wiki tab** (main stage): map of the pages (2D canvas or 3D, switch bottom-right; the choice is shared with the goal tree and the tools graph). *Links* = force layout by links; *Semantic map* = pages positioned by the PCA of their vectors (similar meaning = close; 3 components in 3D). Click a node to read the page (links are clickable), legend chips hide types, search highlights matches; **Index / Log / Health / Schema** open the special files and **↻ Update wiki** syncs and ingests on demand.

### Agent names
Agents keep a technical id (`critic-4`: used by events, containers and goals) but people see a memorable **name** (e.g. *Captain Pixel the Unflappable*), generated from word dictionaries and patterns (`backend/brain/names_data.py`, engine in `names.py`). Names are unique (case-insensitive), stable per agent id, stored in the database (so they travel with projects) and shown in the agent list, live thoughts, 2D/3D views, tools graph and event feed.

* **Style**: the *Names* selector in the Agents panel chooses `off` (plain ids), `all` (each role gets styles that fit it) or one of 13 styles (`classic`, `royal`, `corporate`, `cyber`, `fantasy`, `action`, `nonsense`, `memes`, `heroic`, `pirate`, `scifi`, `cozy`, `office`; word lists in `backend/brain/names_data.py`). It applies to agents created from then on.
* **Choose or roll**: hover an agent and use ✎ to type a name, or ⟳ to roll a new generated one (the id never changes).

### Agent avatars
Each role has an animated avatar (`assets/<Role>.png`: sprite sheets with 3 frames per state; planner, executor, researcher, engineer, critic, reflector, evolver; the service agents `chat`, `status` and `embedding` use the *Reasoner*). The live thoughts panel shows it in the top-right corner of every **active** card (collapsed queued/waiting cards have none). The sheet row follows what the agent is doing: working with a tool, or reasoning (tokens streaming); a card reopened while the agent is queued or waiting shows those rows too. Sources are downscaled and re-aligned into `frontend/public/avatars/` by `frontend/scripts/build_avatars.py` (`pip install pillow`, then `python frontend/scripts/build_avatars.py`); `prefers-reduced-motion` freezes them on the first frame.

### Projects (save / load / new)
The header chip **▣ project name** opens the *Projects* dialog. A project is a full snapshot of Brain, stored in `projects/<id>/` (git-ignored, relocatable with `BRAIN_PROJECTS_DIR`):

| File | Content |
|---|---|
| `brain.db` | consistent SQLite copy: goals, memories, journal, lessons, self-model, events, prompt versions, tools, counters |
| `wiki/` | the markdown wiki |
| `workspace/` | the sandbox workspace: agent-made tools and files |
| `evolvable/` | evolved prompts, hooks and tests |
| `meta.json` | name, date, cycle, main goal, counts, size |

* **Save**: snapshots the live state under a name (same name = same project, overwritten atomically). It can be done while Brain runs.
* **Load**: stops everything, optionally saves the current project first, then replaces database, wiki, workspace and prompts/hooks with the snapshot (restored prompts are committed to the evolvable git history). Brain stays stopped (cycle restored): press ▶ Start to continue from where it was.
* **New project**: optionally saves the current one, then starts from the factory state under a new name, with an optional owner (defaults to the current one) and main goal.
* **Delete** removes a snapshot only. Names must be unique for new projects; ids are validated slugs (no path traversal).

**LM Studio robustness**: if the model gets unloaded mid-request Brain waits for it to reload (a 1-token request triggers LM Studio's JIT loader) and retries. Embeddings are only used when their model is already loaded (`BRAIN_EMBEDDINGS=auto`): requesting an unloaded embedding model makes LM Studio swap models and evict the chat model.

## Dashboard
| View | What it shows |
|---|---|
| Neural network (3D / 2D / Off switch) | Mind core (size = awareness index, pulses while the LLM is busy), agents orbiting by depth, Owner / Internet / Sandbox / Memory nodes, custom tools, message & tool-call pulses, live token stream |
| Goal tree | goal tree on the shared graph canvas ([react-force-graph](https://github.com/vasturiano/react-force-graph)): tidy tree left-to-right in 2D, radial in 3D; colour = status, ring = predicted success, click a node for its details |
| Agents ↔ Tools | which agent uses which tool (link width = call count), same 2D/3D canvas |
| Wiki | map of the wiki (links or semantic layout, 2D or 3D) with a page reader |
| Right panel | awareness gauge + calibration/introspection/success bars, LLM tok/s, CPU, RAM sparklines |
| Left panel | live agents (only agents already streaming or acting show their text; queued, idle, finished or still waiting for the model ones collapse to the title row, click to expand) and the evolving self-model; the live thoughts panel takes half of the stage by default, the 2D view the other half; background jobs show up as agents too: the status report (`status`), chat replies (`chat`) and embedding calls (`embedding`) |
| Dock | live event feed, chat with Brain (the Chat tab shows a badge with the number of Brain messages waiting for your reply), project status, memories, journal, lessons, tools, evolution history |

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
| GET, PUT | `/api/v1/naming` | `{style}`: agent naming style (`off`, `all` or one of the 13 styles above) |
| PUT | `/api/v1/agents/{id}/name` | `{name}` set a name by hand (422 if empty, longer than 40 or taken); `{name: null}` rolls a new generated one |
| GET, PUT | `/api/v1/owner` | `{name}`: who Brain works for. Asked by the dashboard when unknown (first run, after a reset); stored with the project and used in every prompt, question and the wiki; renaming also updates the self-model |
| GET | `/api/v1/projects` | `{current, items}`: active project and saved snapshots (newest first, with stats) |
| POST | `/api/v1/projects/save` | `{name?}` snapshot the live state (default: current project name) |
| POST | `/api/v1/projects/new` | `{name, main_goal?, save_current=true}` start a fresh project (422 if the name exists) |
| POST | `/api/v1/projects/{id}/load` | `{save_current=true}` restore a saved project (404 if unknown) |
| DELETE | `/api/v1/projects/{id}` | delete a saved project |
| GET | `/api/v1/memory?q=` | semantic memory search |
| GET | `/api/v1/memories?q=&kind=&limit=&offset=` | browse memories (newest first) or search them by relevance; returns `items`, per-`kinds` counts and `total` |
| GET | `/api/v1/status` | measurable facts (goals, tools, memories, awareness index...) plus the last stored status report and a `stale` flag |
| POST | `/api/v1/status/refresh` | regenerate the status report: 5-line narrative, estimated progress toward the main goal, what is done and what is missing (falls back to a deterministic report if the model is unavailable) |
| GET | `/api/v1/wiki` | wiki stats: pages, links, vectors, pending items, lint summary, embedding-model availability |
| GET | `/api/v1/wiki/graph` | nodes (with PCA coordinates `sx`,`sy` when vectors exist) and edges |
| GET | `/api/v1/wiki/page?id=` | one page (also `index`, `log`, `lint`, `SCHEMA`) with resolved links and backlinks |
| GET | `/api/v1/wiki/search?q=&k=` | relevance-ranked pages (vectors when available, keywords otherwise) |
| POST | `/api/v1/wiki/ingest` | sync database-derived pages now and fold pending memories/journal into the wiki (LLM) |
| POST | `/api/v1/wiki/embed` | vectorise pages and memories with `BRAIN_EMBED_MODEL` (lets LM Studio load it) |
| GET | `/api/v1/events?limit=` | recent events |
| WS | `/ws` | snapshot, then every event (`agent.*`, `tool.*`, `goal.update`, `system.metrics`, …) |

## Tests
Tests are real (no mocks): they use the live LM Studio, Podman sandbox and internet.
```bash
cd backend && ../.venv/bin/python -m pytest -q
```

## Layout
```
backend/brain/   core: config, db, bus, llm, sandbox, tools, agents, orchestrator, evolution, selfmodel, memory, status, wiki, projects, api
backend/tests/   pytest suite
frontend/src/    dashboard (store, hooks, components)
sandbox/         Containerfile + in-container runners (tool_runner.py, hook_runner.py); workspace/ is the agents' persistent disk
evolvable/       git-versioned prompts, hooks, tests that Brain may rewrite
projects/        saved project snapshots (git-ignored)
```

## FAQ
* **LM Studio errors / empty output** – ensure the model is loaded and the server is started; the header chip shows its state.
* **Podman chip is red** – start the machine (`podman machine start <name>`) and set `BRAIN_PODMAN_CONNECTION`.
* **Runaway cost** – lower `BRAIN_MAX_CYCLES`; the system auto-pauses at the budget.
