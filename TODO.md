# TODO

## Doing
- First long autonomous run; observe planner/critic quality with ternary-bonsai-2-27b and tune prompts.

## To Do
- Context: read_output handles for large tool outputs; limits on reasoning tokens per step.
- Parallel goal execution (currently sequential; LLM concurrency is 1).
- Persist agent streams/tool-call history graph across restarts in the UI.
- Memory consolidation (summarise old episodes).
- Authentication if the dashboard is ever exposed beyond localhost.

## Review
- Awareness index weights (see `selfmodel.py`) – validate they are meaningful over long runs.

## Done
- Context engineering: per-role prompts and tools, working memory with token-budget trimming, mission reminder + forced finish, project state with `depends_on`, prompt validation/audit, focus metric with rollback, context inspector.
- Results panel: progress toward the goal (agents' `report_progress`, system and status points), artifacts with live preview (`publish_artifact` + automatic registration), full tool-run output.
- Projects: save / load / new project snapshots (database, wiki, workspace, evolved prompts/hooks) from the header dialog and `/api/v1/projects`.
- LLM Wiki (`data/wiki`, Karpathy pattern): ingest / query / lint, index + log, vectors from `BRAIN_EMBED_MODEL`, 2D wiki map (links + semantic PCA) with page reader, agent tools `wiki_search/read/note`.
- Dashboard tabs **Status** (5-line project story, progress toward the main goal, done/missing) and **Memories** (browse, filter by kind, search).
- Backend: LLM client, event bus, SQLite, Podman sandbox, tool registry + `create_tool`, ReAct agents, orchestrator, critic, reflector, introspection probe, evolution with git rollback.
- Frontend: 3D neural view, goal tree, tool galaxy, metrics, self-model, chat, journal, event feed.
- Real-service test suite (LM Studio, Podman, internet).
