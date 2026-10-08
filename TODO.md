# TODO

## Doing
- First long autonomous run; observe planner/critic quality with ternary-bonsai-2-27b and tune prompts.

## To Do
- Parallel goal execution (currently sequential; LLM concurrency is 1).
- Persist agent streams/tool-call history graph across restarts in the UI.
- Memory consolidation (summarise old episodes).
- Authentication if the dashboard is ever exposed beyond localhost.

## Review
- Awareness index weights (see `selfmodel.py`) – validate they are meaningful over long runs.

## Done
- LLM Wiki (`data/wiki`, Karpathy pattern): ingest / query / lint, index + log, vectors from `BRAIN_EMBED_MODEL`, 2D wiki map (links + semantic PCA) with page reader, agent tools `wiki_search/read/note`.
- Dashboard tabs **Stato** (5-line project story, progress toward the main goal, done/missing) and **Ricordi** (browse, filter by kind, search).
- Backend: LLM client, event bus, SQLite, Podman sandbox, tool registry + `create_tool`, ReAct agents, orchestrator, critic, reflector, introspection probe, evolution with git rollback.
- Frontend: 3D neural view, goal tree, tool galaxy, metrics, self-model, chat, journal, event feed.
- Real-service test suite (LM Studio, Podman, internet).
