"""Brain's wiki, after Karpathy's "LLM Wiki" pattern: a persistent, interlinked markdown knowledge base
that Brain writes and keeps current, instead of re-deriving what it knows from raw data on every question.

Layers
  raw sources  the SQLite database (events, memories, journal, goals, tools...): read-only for the wiki
  wiki         markdown files under <data>/wiki (open them in Obsidian: [[wikilinks]] + frontmatter)
  schema       wiki/SCHEMA.md, the conventions every page follows
Operations
  ingest       new memories / journal entries are folded into concept, phase, decision... pages (LLM)
  query        `search` (vectors from BRAIN_EMBED_MODEL, keywords as fallback); useful answers are filed back as notes
  lint         broken links, orphans, stale pages, contradictions -> lint.md
index.md catalogues every page; log.md is the append-only chronology (`## [date] kind | title`).
Pages with `managed: auto` are regenerated from the database; `managed: llm` pages are owned by the librarian prompt.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from .llm import LLMError

if TYPE_CHECKING:
    from .core import Brain

FOLDERS = {
    "concept": "concepts", "entity": "entities", "insight": "insights", "decision": "decisions",
    "phase": "phases", "note": "notes", "episode": "episodes", "tool": "tools",
}
FOLDER_TYPE = {v: k for k, v in FOLDERS.items()}
LLM_TYPES = ("concept", "entity", "insight", "decision", "phase")
LLM_FOLDERS = {FOLDERS[t] for t in LLM_TYPES}
SPECIAL = ("index", "log", "SCHEMA", "lint")
LABELS = {
    "meta": "State and meta", "phase": "Phases of the journey", "decision": "Decisions", "concept": "Concepts", "entity": "Entities",
    "insight": "Insights", "episode": "Episodes (concluded goals)", "tool": "Tools created", "note": "Notes and filed answers",
}
ORDER = list(LABELS)

ID_RE = re.compile(r"[a-z0-9][a-z0-9_\-]*(/[a-z0-9][a-z0-9_\-]*)?")
SRC_RE = re.compile(r"[a-z]+:[\w\-./]{1,40}")
WIKILINK = re.compile(r"\[\[([^\]|#]+?)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
SOURCE_LINK = re.compile(r"\[\[((?:memory|journal):\d+)\]\]")
FRONT = re.compile(r"\A---\n(.*?)\n---\n?", re.S)

QUIET_SECONDS = 10  # events are batched: pages are rebuilt once things settle
MAX_DIRTY_SECONDS = 60
INGEST_MIN_ITEMS = 8
INGEST_MAX_AGE = 1800
STALE_DAYS = 14

SCHEMA = """# Brain wiki schema

This wiki is written and maintained by Brain ("LLM Wiki" pattern): knowledge is compiled once and kept current,
not rebuilt for every question. The owner reads it (Obsidian works too); Brain writes it.

## Layers
- **Raw sources**: Brain's database (events, memories, journal, goals, tools). The wiki reads them, never modifies them.
- **Wiki**: the markdown files in here.
- **Schema**: this file.

## Pages
Every page is `folder/slug.md` with frontmatter:

    ---
    title: Title
    type: concept | entity | insight | decision | phase | note | episode | tool | meta
    summary: one line
    updated: ISO date
    managed: auto | llm | user
    sources: [memory:12, journal:3]
    tags: [a, b]
    ---

- `managed: auto`: page regenerated from the database (status, lessons, evolution, self-model, episodes, tools): do not edit it by hand.
- `managed: llm`: page written by the librarian (concepts, entities, insights, decisions, phases, notes).
- `managed: user`: the owner's own page: Brain reads and indexes it but never overwrites it.
- Link with `[[folder/slug]]` (or `[[Title]]`). Every page must link at least one other; links to missing pages are "wanted pages".
- A **phase** (`phases/`) is a period or turning point of the journey: date, what changed, why it matters.
- Contradictions are flagged with a line starting with `⚠ Contradiction:` (old and new version).

## Special files
- `index.md`: catalogue of all pages by category, with summary and date. It regenerates itself.
- `log.md`: append-only chronology. Every entry starts with `## [YYYY-MM-DD HH:MM] kind | title`
  (kinds: goal, tool, evolution, lesson, status, ingest, lint, query), so `grep "^## \\[" log.md | tail -5` shows the latest ones.
- `lint.md`: result of the last health check.

## Operations
- **Ingest**: new memories and journal entries are folded into existing pages or new pages; index and log are updated.
- **Query**: search the wiki (vectors + keywords) before researching again; useful answers are filed under `notes/`.
- **Lint**: broken links, orphan pages, long-stale pages, open contradictions, pages without a vector.
"""

INGEST_PROMPT = """You are the librarian of Brain's wiki ("LLM Wiki" pattern): you do not answer questions, you MAINTAIN a markdown knowledge base that accumulates over time.
You receive NEW raw information (memories and journal entries) and the list of pages that already exist. Fold the news in:
- create new pages for concepts, entities (people, tools, services), insights, decisions and significant PHASES of the journey (type "phase": a period or turning point, with the date and what changed);
- if an existing page is relevant, UPDATE it by reusing its id and returning the COMPLETE rewritten body: keep the previous facts, add the new ones, and if new information contradicts an old one write a line starting with "⚠ Contradiction:" with both versions;
- link pages with [[id]] (ids of existing pages or ones you create now): every page links at least one; if you are given WANTED PAGES and you have the information, create them;
- use ONLY the information provided, no inventions; write in English, concise (body max 1500 characters, markdown with ## headings and lists);
- sources ([memory:12], [journal:3]) go ONLY in the "sources" field, never as [[...]] links in the body;
- at most 5 pages; ignore noise (trivial or repeated things).
Reply ONLY with JSON: {"pages": [{"id": "concepts/slug", "title": "...", "type": "concept|entity|insight|decision|phase", "summary": "one line", "body": "markdown", "sources": ["memory:12", "journal:3"]}], "log": "one line about what you integrated"}
Ids have the form folder/slug, with folder among concepts, entities, insights, decisions, phases."""


# ------------------------------------------------------------------ helpers
def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:60] or "page"


def _one_line(s: object, n: int = 160) -> str:
    return " ".join(str(s or "").split())[:n]


def _plain(s: object, n: int) -> str:
    """Untrusted text: defuse wikilinks so it cannot create links by accident."""
    return str(s or "").replace("[[", "[ [").replace("]]", "] ]")[:n]


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w{3,}", s.lower()))


def _date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "-"


def _hash(title: str, type_: str, summary: str, body: str, sources: list[str], tags: list[str]) -> str:
    return hashlib.sha1(json.dumps([title, type_, summary, body, sources, tags], ensure_ascii=False).encode()).hexdigest()[:16]


def _parse(text: str) -> tuple[dict[str, str], str]:
    m = FRONT.match(text)
    meta: dict[str, str] = {}
    if m:
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                meta[k.strip()] = v.strip()
        text = text[m.end():]
    return meta, text.strip()


def _list(v: str) -> list[str]:
    v = (v or "").strip()
    if v.startswith("[") and v.endswith("]"):
        v = v[1:-1]
    return [x.strip() for x in v.split(",") if x.strip()]


def _links(body: str) -> list[str]:
    return sorted({t.strip() for t in WIKILINK.findall(body) if t.strip()})


def _pca3(vectors: list[np.ndarray]) -> list[tuple[float, float, float]]:
    """Semantic map: first three principal components of the page vectors, scaled to [-1, 1] (2D view uses x,y)."""
    x = np.array(vectors, dtype=float)
    x = x - x.mean(axis=0)
    _, _, vt = np.linalg.svd(x, full_matrices=False)
    xy = x @ vt[:3].T
    if xy.shape[1] < 3:
        xy = np.hstack([xy, np.zeros((len(xy), 3 - xy.shape[1]))])
    scale = np.abs(xy).max() or 1.0
    return [(round(float(a / scale), 4), round(float(b / scale), 4), round(float(c / scale), 4)) for a, b, c in xy]


class Wiki:
    def __init__(self, brain: "Brain"):
        self.b = brain
        self.dir: Path = brain.settings.wiki_dir
        self._task: asyncio.Task | None = None
        self._ingest_task: asyncio.Task | None = None
        self._ingest_lock = asyncio.Lock()
        self._dirty = False
        self._dirty_since = 0.0
        self._dirty_at = 0.0
        self._changed = 0
        self._next_ingest_at = 0.0
        self._last_maint = 0.0
        self._vec: dict[str, tuple[str, np.ndarray]] = {}
        self.embed_error = ""

    # --------------------------------------------------------------- lifecycle
    async def init(self) -> None:
        for sub in ("", *FOLDERS.values()):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)
        if not (self.dir / "SCHEMA.md").exists():
            (self.dir / "SCHEMA.md").write_text(SCHEMA)
        self._ensure_log()
        await self.sync_all()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        tasks = [t for t in (self._task, self._ingest_task) if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._task = self._ingest_task = None

    async def reload(self, wipe: bool = False) -> None:
        """Re-read the wiki from disk (after its folder was replaced) or, with wipe=True, start it from scratch."""
        if self._ingest_task:
            self._ingest_task.cancel()
            await asyncio.gather(self._ingest_task, return_exceptions=True)
            self._ingest_task = None
        if wipe:
            shutil.rmtree(self.dir, ignore_errors=True)
        self._vec.clear()
        self._dirty = False
        self.embed_error = ""
        await self.init()

    async def reset(self) -> None:
        await self.reload(wipe=True)

    # ------------------------------------------------------------------ files
    def _path(self, pid: str) -> Path:
        if pid not in SPECIAL and not ID_RE.fullmatch(pid):
            raise ValueError(f"invalid page id: {pid!r}")
        p = (self.dir / f"{pid}.md").resolve()
        if self.dir.resolve() not in p.parents:
            raise ValueError("path outside the wiki")
        return p

    def _ensure_log(self) -> None:
        p = self.dir / "log.md"
        if not p.exists():
            p.write_text("# Log\n\nAppend-only chronology of the wiki. Every entry: `## [date] kind | title`.\n\n")

    def _write_special(self, name: str, text: str) -> bool:
        p = self.dir / f"{name}.md"
        if p.exists() and p.read_text() == text:
            return False
        p.write_text(text)
        return True

    def log(self, kind: str, title: str, detail: str = "", key: str | None = None) -> None:
        """Append to log.md; `key` makes an entry idempotent (e.g. one line per goal outcome). `detail` must be pre-sanitised."""
        if key:
            seen = self.b.db.kv_get("wiki_logged", [])
            if key in seen:
                return
            self.b.db.kv_set("wiki_logged", [*seen, key][-1500:])
        self._ensure_log()
        entry = f"## [{datetime.now().strftime('%Y-%m-%d %H:%M')}] {kind} | {_one_line(title, 120)}\n"
        entry += f"{detail.strip()}\n\n" if detail.strip() else "\n"
        with (self.dir / "log.md").open("a") as f:
            f.write(entry)

    # ------------------------------------------------------------------ pages
    def write_page(
        self, pid: str, title: str, type_: str, summary: str, body: str, *,
        sources: list[str] | tuple[str, ...] = (), tags: list[str] | tuple[str, ...] = (), managed: str = "auto",
    ) -> bool:
        """Create/update a page file and its index row; returns False when nothing changed."""
        path = self._path(pid)
        title, summary, body = _one_line(title, 100), _one_line(summary, 200), body.strip()
        summary = summary or _one_line(body.replace("#", " "), 200)  # same fallback as scan()
        srcs = [s for s in dict.fromkeys(sources) if SRC_RE.fullmatch(s)]
        tgs = [slugify(t) for t in tags][:8]
        h = _hash(title, type_, summary, body, srcs, tgs)
        row = self.b.db.one("SELECT hash FROM wiki_pages WHERE id=?", (pid,))
        if row and row["hash"] == h and path.exists():
            return False
        now = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\ntitle: {title}\ntype: {type_}\nsummary: {summary}\nupdated: {datetime.fromtimestamp(now).isoformat(timespec='seconds')}\n"
            f"managed: {managed}\nsources: [{', '.join(srcs)}]\ntags: [{', '.join(tgs)}]\n---\n\n{body}\n"
        )
        self._upsert(pid, title, type_, summary, body, h, managed, now)
        self._changed += 1
        return True

    def _upsert(self, pid: str, title: str, type_: str, summary: str, body: str, h: str, managed: str, ts: float) -> None:
        self.b.db.execute(
            "INSERT INTO wiki_pages(id,title,type,summary,body,updated,hash,managed,links,embedding) VALUES(?,?,?,?,?,?,?,?,?,NULL) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title,type=excluded.type,summary=excluded.summary,body=excluded.body,"
            "updated=excluded.updated,hash=excluded.hash,managed=excluded.managed,links=excluded.links,embedding=NULL",
            (pid, title, type_, summary, body, ts, h, managed, json.dumps(_links(body))),
        )
        self._vec.pop(pid, None)

    def scan(self) -> int:
        """Pick up pages edited or added by hand (e.g. in Obsidian) and drop rows of deleted files."""
        seen: set[str] = set()
        changed = 0
        for p in sorted(self.dir.rglob("*.md")):
            pid = p.relative_to(self.dir).with_suffix("").as_posix()
            if pid in SPECIAL or not ID_RE.fullmatch(pid):
                continue
            seen.add(pid)
            meta, body = _parse(p.read_text(errors="replace"))
            title = _one_line(meta.get("title") or (re.search(r"^#\s+(.+)$", body, re.M) or [None, pid.rsplit("/", 1)[-1]])[1], 100)
            type_ = meta.get("type") or FOLDER_TYPE.get(pid.split("/")[0], "note")
            summary = _one_line(meta.get("summary") or body.replace("#", " "), 200)
            h = _hash(title, type_, summary, body, _list(meta.get("sources", "")), _list(meta.get("tags", "")))
            row = self.b.db.one("SELECT hash FROM wiki_pages WHERE id=?", (pid,))
            if not row or row["hash"] != h:
                self._upsert(pid, title, type_, summary, body, h, meta.get("managed") or "user", p.stat().st_mtime)
                changed += 1
        for r in self.b.db.query("SELECT id FROM wiki_pages"):
            if r["id"] not in seen:
                self.b.db.execute("DELETE FROM wiki_pages WHERE id=?", (r["id"],))
                self._vec.pop(r["id"], None)
                changed += 1
        self._changed += changed
        return changed

    def _resolver(self, rows: list[dict] | None = None):
        rows = rows if rows is not None else self.b.db.query("SELECT id,title FROM wiki_pages")
        by_id = {r["id"].lower(): r["id"] for r in rows}
        by_slug: dict[str, str] = {}
        for r in rows:
            by_slug.setdefault(r["id"].rsplit("/", 1)[-1].lower(), r["id"])
        by_title = {r["title"].lower(): r["id"] for r in rows}
        specials = {s.lower(): s for s in SPECIAL}

        def resolve(target: str) -> str | None:
            k = target.strip().lower().removesuffix(".md")
            return by_id.get(k) or by_slug.get(k) or by_title.get(k) or by_slug.get(slugify(target)) or specials.get(k)
        return resolve

    def _edges(self, rows: list[dict]) -> set[tuple[str, str]]:
        res = self._resolver(rows)
        ids = {r["id"] for r in rows}
        edges: set[tuple[str, str]] = set()
        for r in rows:
            for t in json.loads(r["links"] or "[]"):
                tid = res(t)
                if tid and tid in ids and tid != r["id"]:
                    edges.add((r["id"], tid))
        return edges

    def read_page(self, pid: str) -> dict | None:
        if pid in SPECIAL:
            p = self.dir / f"{pid}.md"
            if not p.exists():
                return None
            body = p.read_text()
            res = self._resolver()
            titles = {r["id"]: r["title"] for r in self.b.db.query("SELECT id,title FROM wiki_pages")}
            links = [{"target": t, "id": (tid := res(t)), "title": titles.get(tid, tid) if tid else None} for t in _links(body)]
            return {"id": pid, "title": pid, "type": "special", "summary": "", "body": body, "updated": p.stat().st_mtime,
                    "managed": "auto", "sources": [], "tags": [], "links": links, "backlinks": []}
        if not ID_RE.fullmatch(pid):
            return None
        row = self.b.db.one("SELECT id,title,type,summary,body,updated,managed FROM wiki_pages WHERE id=?", (pid,))
        if not row:
            return None
        rows = self.b.db.query("SELECT id,title,links FROM wiki_pages")
        res = self._resolver(rows)
        titles = {r["id"]: r["title"] for r in rows}
        meta, _ = _parse(self._path(pid).read_text(errors="replace")) if self._path(pid).exists() else ({}, "")
        out = []
        for t in _links(row["body"]):
            tid = res(t)
            out.append({"target": t, "id": tid, "title": titles.get(tid, tid) if tid else None})
        back = [{"id": r["id"], "title": r["title"]} for r in rows if r["id"] != pid and any(res(t) == pid for t in json.loads(r["links"] or "[]"))]
        return {**row, "sources": _list(meta.get("sources", "")), "tags": _list(meta.get("tags", "")), "links": out, "backlinks": back}

    # ----------------------------------------------------- generated pages
    async def sync_all(self) -> int:
        """Bring every database-derived page up to date, then rebuild index.md and lint.md. Returns pages changed."""
        self._changed = 0
        self.scan()
        self._sync_episodes()
        self._sync_tools()
        self._sync_meta()
        self._sync_status()
        self._rebuild_index()
        self.lint()
        n = self._changed
        if n:
            await self.b.bus.publish("wiki.update", None, changed=n)
        return n

    def _sync_status(self) -> None:
        b = self.b
        cur = b.status.current()
        f, rep = cur["facts"], cur["report"]
        m, g = f["metrics"], f["goals"]
        lines = ["# Project status", ""]
        if rep:
            lines += [f"{i}. {_plain(x, 220)}" for i, x in enumerate(rep["lines"], 1)]
            src = "model estimate" if rep["source"] == "llm" else "measurable indicator"
            lines += ["", f"**Progress toward the goal:** {round(rep['progress'])}% ({src}, report of {_date(rep['ts'])})"]
        else:
            lines.append("_The status story has not been generated yet (Status tab of the dashboard)._")
        lines += [
            "", f"**Main goal:** {_plain(f['goal'], 600)}", "", "## Numbers", "",
            "| goals succeeded | failed | queued | tools | memories | lessons | evolutions | awareness index |", "|---|---|---|---|---|---|---|---|",
            f"| {g['done']} | {g['failed']} | {g['pending'] + g['active']} | {m['tools']} | {m['memories']} | {m['lessons']} | {m['evolutions_applied']} | {m['awareness_index']:.2f} |",
        ]
        if rep and rep["done"]:
            lines += ["", "## Done", *[f"- {_plain(x, 200)}" for x in rep["done"]]]
        if rep and rep["missing"]:
            lines += ["", "## Missing", *[f"- {_plain(x, 200)}" for x in rep["missing"]]]
        if f["next"]:
            lines += ["", "## Queued", *[f"- {_plain(x, 160)}" for x in f["next"]]]
        db = b.db
        lines += ["", "## Explore", "", "- [[lessons]] · [[evolution]] · [[self-model]] · [[index]] · [[log]] · [[lint]]"]
        for title, typ, n in (("Phases of the journey", "phase", 10), ("Decisions", "decision", 6), ("Recent episodes", "episode", 8)):
            rows = db.query("SELECT id,title,summary FROM wiki_pages WHERE type=? ORDER BY updated DESC LIMIT ?", (typ, n))
            if rows:
                lines += ["", f"## {title}", *[f"- [[{r['id']}]] — {_plain(r['summary'], 120)}" for r in rows]]
        tools = db.query("SELECT id,summary FROM wiki_pages WHERE type='tool' ORDER BY id")
        if tools:
            lines += ["", "## Tools created", *[f"- [[{r['id']}]] — {_plain(r['summary'], 120)}" for r in tools]]
        summary = (f"State {f['state']}: {g['done']} goals succeeded, {g['failed']} failed, {g['pending'] + g['active']} queued; "
                   f"{m['tools']} tools, {m['memories']} memories, index {m['awareness_index']:.2f}.")
        self.write_page("status", "Project status", "meta", summary, "\n".join(lines))

    def _sync_meta(self) -> None:
        b = self.b
        lessons = sorted(b.lessons.all(), key=lambda i: (i["count"], i["ts"]), reverse=True)
        body = ["# Lessons learned", "", "Rules derived from errors and injected into the agents' prompts. Back to [[status]].", ""]
        by_kind: dict[str, list[dict]] = defaultdict(list)
        for it in lessons:
            by_kind[it["kind"]].append(it)
        for kind, items in by_kind.items():
            body += [f"## {kind}", *[f"- {_plain(i['text'], 400)} _(seen {i['count']}x, {_date(i['ts'])})_" for i in items], ""]
        if not lessons:
            body.append("_No lessons yet._")
        self.write_page("lessons", "Lessons learned", "meta", f"{len(lessons)} lessons derived from errors.", "\n".join(body))

        evo = b.evolution.history(50)
        body = ["# Evolution", "", "Changes to prompts and hooks, with outcome (applied / rolled_back / rejected). Back to [[status]].", ""]
        if evo:
            body += ["| date | kind | target | outcome | reason |", "|---|---|---|---|---|"]
            body += [f"| {_date(e['ts'])} | {e['kind']} | {e['target']} | {e['status']} | {_plain(e['reason'], 160).replace('|', '/')} |" for e in evo]
        else:
            body.append("_No evolution yet._")
        applied = sum(1 for e in evo if e["status"] == "applied")
        self.write_page("evolution", "Evolution", "meta", f"{len(evo)} changes proposed, {applied} applied.", "\n".join(body))

        sm = b.selfmodel.get()
        body = ["# Self-model", "", f"Revision {sm.get('revision', 0)}. Back to [[status]].", ""]
        for key in ("identity", "purpose", "about_user", "about_world"):
            body += [f"## {key}", _plain(sm.get(key, ""), 600), ""]
        for key in ("capabilities", "limitations", "open_questions", "hypotheses"):
            items = sm.get(key) or []
            body += [f"## {key}", *([f"- {_plain(x, 300)}" for x in items] or ["_none_"]), ""]
        self.write_page("self-model", "Self-model", "meta", f"How Brain describes itself (revision {sm.get('revision', 0)}).", "\n".join(body))

    def _sync_episodes(self) -> None:
        db = self.b.db
        verdicts: dict[int, dict] = {}
        for r in db.query("SELECT data FROM events WHERE type='goal.verdict' ORDER BY seq DESC LIMIT 500"):
            d = json.loads(r["data"])
            verdicts.setdefault(d.get("goal_id"), d)
        for g in db.query("SELECT * FROM goals WHERE parent_id IS NOT NULL AND status IN ('done','failed') ORDER BY id"):
            ok = g["status"] == "done"
            exp = "-" if g["expected_success"] is None else f"{g['expected_success']:.1f}"
            body = [
                f"# Goal #{g['id']}: {_plain(g['title'], 150)}", "",
                f"**Outcome:** {'succeeded' if ok else 'failed'} · priority {g['priority']:.1f} · expected success {exp} · attempts {g['attempts']} · closed on {_date(g['updated'])}",
                "", "## Description", _plain(g["description"], 1200) or "_none_", "", "## Result", _plain(g["result"], 1500) or "_none_",
            ]
            v = verdicts.get(g["id"])
            if v:
                body += ["", "## Critic evaluation", f"{v.get('verdict')} (score {v.get('score')}): {_plain(v.get('feedback'), 500)}"]
            body += ["", "Back to [[status]]."]
            self.write_page(
                f"episodes/goal-{g['id']}", f"Goal #{g['id']}: {g['title']}", "episode",
                f"{'Succeeded' if ok else 'Failed'} — {g['result'] or g['title']}", "\n".join(body), tags=[g["status"], g["role"] or "executor"],
            )

    def _sync_tools(self) -> None:
        for t in self.b.db.query("SELECT * FROM tools"):
            if not re.fullmatch(r"[a-z][a-z0-9_]{2,30}", t["name"]):
                continue
            body = [
                f"# Tool `{t['name']}`", "", _plain(t["description"], 400), "",
                f"**Status:** {t['status']} · calls {t['calls']} · errors {t['failures']} · created on {_date(t['created'])}", "",
                "## Parameters", f"`{_plain(t['params'], 400)}`", "", "## Test results", "```", (t["test_output"] or "")[-600:].replace("```", "'''"), "```", "",
                "Back to [[status]].",
            ]
            self.write_page(f"tools/{t['name']}", t["name"], "tool", t["description"] or t["name"], "\n".join(body), tags=[t["status"]])

    def _rebuild_index(self) -> None:
        rows = self.b.db.query("SELECT id,title,type,summary,updated FROM wiki_pages ORDER BY updated DESC")
        by: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            by["meta" if r["type"] == "meta" else r["type"]].append(r)
        lines = [
            "# Index", "",
            f"{len(rows)} pages. Chronology: [[log]] · Conventions: [[SCHEMA]] · Health: [[lint]]. It regenerates itself.", "",
        ]
        for t in [*ORDER, *sorted(k for k in by if k not in ORDER)]:
            if by.get(t):
                lines += [f"## {LABELS.get(t, t)} ({len(by[t])})", *[f"- [[{r['id']}]] — {_plain(r['summary'], 140)} _({_date(r['updated'])})_" for r in by[t]], ""]
        self._write_special("index", "\n".join(lines))

    # ------------------------------------------------------------------- lint
    def lint(self) -> dict:
        """Health check: broken links (= wanted pages), orphans, stale pages, open contradictions, pages without vectors."""
        db = self.b.db
        rows = db.query("SELECT id,title,type,summary,body,updated,managed,links,embedding IS NOT NULL AS vec FROM wiki_pages")
        res = self._resolver(rows)
        ids = {r["id"] for r in rows}
        broken: dict[str, list[str]] = defaultdict(list)
        inbound: Counter[str] = Counter()
        for r in rows:
            for t in json.loads(r["links"] or "[]"):
                tid = res(t)
                if tid is None:
                    broken[t].append(r["id"])
                elif tid in ids and tid != r["id"]:
                    inbound[tid] += 1
        now = time.time()
        report = {
            "broken": [{"target": t, "from": sorted(set(f))} for t, f in sorted(broken.items(), key=lambda kv: -len(kv[1]))],
            "orphans": sorted(r["id"] for r in rows if r["managed"] != "auto" and not inbound[r["id"]]),
            "stale": sorted(r["id"] for r in rows if r["managed"] == "llm" and now - r["updated"] > STALE_DAYS * 86400),
            "contradictions": sorted(r["id"] for r in rows if "⚠ Contradiction" in (r["body"] or "")),
            "no_summary": sorted(r["id"] for r in rows if not (r["summary"] or "").strip()),
            "unembedded": sum(1 for r in rows if not r["vec"]),
            "pages": len(rows),
        }
        report["total"] = sum(len(report[k]) for k in ("broken", "orphans", "stale", "contradictions", "no_summary"))
        out = ["# Health check (lint)", "", f"{report['pages']} pages · {report['total']} findings · {report['unembedded']} without a vector.", ""]
        sections = [
            ("Missing pages (broken links)", [f"- `{b['target']}` cited by " + ", ".join(f"[[{x}]]" for x in b["from"]) for b in report["broken"]]),
            ("Orphan pages (no inbound links)", [f"- [[{x}]]" for x in report["orphans"]]),
            (f"Not updated for over {STALE_DAYS} days", [f"- [[{x}]]" for x in report["stale"]]),
            ("Open contradictions", [f"- [[{x}]]" for x in report["contradictions"]]),
            ("Without a summary", [f"- [[{x}]]" for x in report["no_summary"]]),
        ]
        for title, items in sections:
            out += [f"## {title}", *(items or ["_none_"]), ""]
        self._write_special("lint", "\n".join(out))
        prev = db.kv_get("wiki_lint")
        db.kv_set("wiki_lint", {k: report[k] for k in ("total", "pages", "unembedded")} | {"ts": now})
        db.kv_set("wiki_wanted", report["broken"][:10])
        if prev is None or prev.get("total") != report["total"]:
            self.log("lint", f"{report['total']} findings on {report['pages']} pages", "See [[lint]].")
        return report

    # --------------------------------------------------------------- vectors
    def _prefix(self, kind: str) -> str:
        return {"doc": "search_document: ", "query": "search_query: "}[kind] if "nomic" in self.b.settings.embed_model.lower() else ""

    async def embed_pending(self, force: bool = False) -> dict:
        """Vectorise pages (and memories) that have none, with BRAIN_EMBED_MODEL. `force` lets LM Studio load the model."""
        b = self.b
        if not force and not await b.llm.embeddings_available():
            self.embed_error = ""
            return {"embedded": 0, "available": False}
        done = 0
        for _ in range(40):
            rows = b.db.query("SELECT id,title,summary,body,hash FROM wiki_pages WHERE embedding IS NULL LIMIT 16")
            if not rows:
                break
            texts = [f"{self._prefix('doc')}{r['title']}\n{r['summary']}\n{(r['body'] or '')[:1500]}" for r in rows]
            try:
                vecs = await b.llm.embed(texts, force=force)
            except LLMError as e:
                self.embed_error = str(e)[:200]
                return {"embedded": done, "available": False, "error": self.embed_error}
            for r, v in zip(rows, vecs):
                b.db.execute("UPDATE wiki_pages SET embedding=? WHERE id=? AND hash=?", (json.dumps(v), r["id"], r["hash"]))
            done += len(rows)
        self.embed_error = ""
        mem = 0
        for _ in range(40):
            n = await b.memory.backfill_embeddings(50, force=force)
            mem += n
            if n < 50:
                break
        if done or mem:
            await b.bus.publish("wiki.update", None, changed=done, vectors=True)
        if force:  # loading the embedding model can make LM Studio evict the chat model: bring it back now
            await b.llm.ensure_loaded()
        return {"embedded": done, "memories": mem, "available": True}

    def _vector(self, row: dict) -> np.ndarray | None:
        if not row["embedding"]:
            return None
        cached = self._vec.get(row["id"])
        if cached and cached[0] == row["hash"]:
            return cached[1]
        v = np.array(json.loads(row["embedding"]), dtype=float)
        self._vec[row["id"]] = (row["hash"], v)
        return v

    # ------------------------------------------------------------------ query
    async def search(self, query: str, k: int = 5) -> list[dict]:
        """Relevance-ranked pages: cosine similarity when vectors exist, keyword overlap otherwise."""
        rows = self.b.db.query("SELECT id,title,type,summary,body,hash,embedding FROM wiki_pages")
        if not rows or not query.strip():
            return []
        qv = None
        if any(r["embedding"] for r in rows):
            try:
                qv = np.array((await self.b.llm.embed([self._prefix("query") + query[:1000]]))[0], dtype=float)
            except LLMError:
                qv = None
        qt = _tokens(query)
        scored = []
        for r in rows:
            kw = len(qt & _tokens(f"{r['title']} {r['summary']} {r['body']}")) / (len(qt) + 1e-9)
            score = kw
            v = self._vector(r) if qv is not None else None
            if v is not None:
                cos = float(v @ qv / (np.linalg.norm(v) * np.linalg.norm(qv) + 1e-9))
                score = max(kw, (cos - 0.45) / 0.55)
            if score >= 0.15:
                scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"id": r["id"], "title": r["title"], "type": r["type"], "summary": r["summary"], "score": round(s, 3)} for s, r in scored[:k]]

    async def context(self, query: str, k: int = 3, chars: int = 700) -> str:
        """Snippets of the most relevant pages, ready to be put in a prompt."""
        try:
            hits = await self.search(query, k)
        except Exception:  # noqa: BLE001 - the wiki must never break chat or agents
            return ""
        parts = []
        for h in hits:
            row = self.b.db.one("SELECT body FROM wiki_pages WHERE id=?", (h["id"],))
            parts.append(f"[[{h['id']}]] {h['title']}: {h['summary']}\n{(row['body'] if row else '')[:chars]}")
        return "\n\n".join(parts)

    async def note(self, title: str, body: str, summary: str = "", source: str = "") -> str:
        """File a useful answer back into the wiki so explorations compound."""
        title, body = _one_line(title, 100), body.strip()
        if len(title) < 3 or len(body) < 20:
            raise ValueError("a title (3+ characters) and a body (20+ characters) are required")
        pid = f"notes/{slugify(title)}"
        self.write_page(pid, title, "note", summary or body[:160], body, sources=[source] if source else [], managed="llm")
        self.log("query", f"Filed: {title}", f"[[{pid}]]")
        self._refresh_meta()
        self._touch()
        await self.b.bus.publish("wiki.update", None, changed=1)
        return pid

    def _refresh_meta(self) -> None:
        self._rebuild_index()
        self.lint()

    # ----------------------------------------------------------------- ingest
    def pending_items(self) -> int:
        cur = self.b.db.kv_get("wiki_cursor", {"memory": 0, "journal": 0})
        q = self.b.db.one
        return q("SELECT COUNT(*) c FROM memories WHERE id>?", (cur["memory"],))["c"] + q("SELECT COUNT(*) c FROM journal WHERE id>?", (cur["journal"],))["c"]

    async def ingest(self, passes: int = 1) -> dict:
        """Fold new memories and journal entries into the wiki (LLM librarian). Cursors advance only on success."""
        if self._ingest_lock.locked():
            return {"pages": 0, "items": 0, "busy": True}
        async with self._ingest_lock:
            total = {"pages": 0, "items": 0}
            for _ in range(max(1, passes)):
                out = await self._ingest_once()
                total["pages"] += out.get("pages", 0)
                total["items"] += out.get("items", 0)
                if out.get("error") or not out.get("items"):
                    total.update({k: v for k, v in out.items() if k == "error"})
                    break
            return total

    async def _ingest_once(self) -> dict:
        b = self.b
        cur = b.db.kv_get("wiki_cursor", {"memory": 0, "journal": 0})
        mems = b.db.query("SELECT id,kind,text FROM memories WHERE id>? ORDER BY id LIMIT 20", (cur["memory"],))
        jr = b.db.query("SELECT id,kind,text FROM journal WHERE id>? ORDER BY id LIMIT 12", (cur["journal"],))
        if not mems and not jr:
            return {"pages": 0, "items": 0}
        items = [f"[memory:{m['id']}] ({m['kind']}) {_one_line(m['text'], 350)}" for m in mems]
        items += [f"[journal:{j['id']}] ({j['kind']}) {_one_line(j['text'], 450)}" for j in jr]
        index = b.db.query("SELECT id,type,summary FROM wiki_pages WHERE managed!='auto' ORDER BY updated DESC LIMIT 80")
        related = await self.search(" ".join(items)[:700], 4)
        rel_txt = ""
        for h in related:
            row = b.db.one("SELECT body,managed FROM wiki_pages WHERE id=?", (h["id"],))
            if row and row["managed"] != "auto":
                rel_txt += f"### {h['id']}\n{row['body'][:1400]}\n\n"
        wanted = [x["target"] for x in (b.db.kv_get("wiki_wanted") or [])][:6]
        prompt = (
            "EXISTING PAGES (id | type | summary):\n" + ("\n".join(f"{r['id']} | {r['type']} | {r['summary'][:100]}" for r in index) or "(none)")
            + f"\n\nRELEVANT PAGES (current content):\n{rel_txt or '(none)'}"
            + (f"\nWANTED PAGES (broken links to create if you have the information):\n" + "\n".join(f"- {w}" for w in wanted) + "\n" if wanted else "")
            + "\nNEW INFORMATION:\n" + "\n".join(items)
        )
        try:
            out = await b.llm.chat_json(
                [{"role": "system", "content": INGEST_PROMPT}, {"role": "user", "content": prompt}],
                agent="librarian", purpose="wiki ingest", temperature=0.3, max_tokens=2500,
            )
        except LLMError as e:
            self._next_ingest_at = time.time() + 600
            await b.bus.publish("system.log", None, level="warn", text=f"Wiki ingest: {e}")
            return {"pages": 0, "items": 0, "error": str(e)[:200]}
        written = [pid for p in (out.get("pages") or [])[:5] if isinstance(p, dict) and (pid := self._ingest_page(p))]
        b.db.kv_set("wiki_cursor", {"memory": max([cur["memory"], *[m["id"] for m in mems]]), "journal": max([cur["journal"], *[j["id"] for j in jr]])})
        b.db.kv_set("wiki_last_ingest", time.time())
        n_items = len(items)
        self.log(
            "ingest", f"{len(written)} pages from {len(mems)} memories and {len(jr)} journal entries",
            (_plain(out.get("log"), 300) + "\n" if out.get("log") else "") + " ".join(f"[[{p}]]" for p in written),
        )
        self._refresh_meta()
        self._touch()
        await b.bus.publish("wiki.update", None, changed=len(written), ingest=True)
        return {"pages": len(written), "items": n_items}

    def _ingest_page(self, p: dict) -> str | None:
        title = _one_line(p.get("title"), 100)
        body = SOURCE_LINK.sub(r"(\1)", str(p.get("body") or "").strip()[:4000])  # sources are not pages
        if len(title) < 3 or len(body) < 40:
            return None
        raw = str(p.get("id") or "").strip().lower().removesuffix(".md")
        if ID_RE.fullmatch(raw) and "/" in raw and raw.split("/")[0] in LLM_FOLDERS:
            pid, type_ = raw, FOLDER_TYPE[raw.split("/")[0]]
        else:
            type_ = p.get("type") if p.get("type") in LLM_TYPES else "concept"
            pid = f"{FOLDERS[type_]}/{slugify(title)}"
        row = self.b.db.one("SELECT managed FROM wiki_pages WHERE id=?", (pid,))
        if row and row["managed"] == "user":
            return None  # never overwrite the owner's own pages
        old = []
        if row and self._path(pid).exists():
            old = _list(_parse(self._path(pid).read_text(errors="replace"))[0].get("sources", ""))
        sources = [*old, *[str(s) for s in (p.get("sources") or []) if isinstance(s, str)]]
        self.write_page(pid, title, type_, p.get("summary") or body[:160], body, sources=sources, managed="llm")
        return pid

    # ------------------------------------------------------------------ views
    def graph(self) -> dict:
        rows = self.b.db.query("SELECT id,title,type,summary,updated,managed,hash,links,embedding FROM wiki_pages")
        edges = self._edges(rows)
        deg: Counter[str] = Counter()
        for a, c in edges:
            deg[a] += 1
            deg[c] += 1
        vecs = {r["id"]: v for r in rows if (v := self._vector(r)) is not None}
        sem: dict[str, tuple[float, float, float]] = {}
        if len(vecs) >= 3:
            try:
                sem = dict(zip(vecs, _pca3(list(vecs.values()))))
            except np.linalg.LinAlgError:
                sem = {}
        nodes = [
            {"id": r["id"], "title": r["title"], "type": r["type"], "summary": r["summary"], "updated": r["updated"], "managed": r["managed"],
             "degree": deg[r["id"]], "embedded": r["id"] in vecs,
             **dict(zip(("sx", "sy", "sz"), sem.get(r["id"], (None, None, None))))}
            for r in rows
        ]
        return {"nodes": nodes, "edges": [{"source": a, "target": c} for a, c in sorted(edges)]}

    async def stats(self) -> dict:
        b = self.b
        rows = b.db.query("SELECT id,title,links FROM wiki_pages")
        mem = b.db.one("SELECT COUNT(*) c, SUM(embedding IS NOT NULL) e FROM memories")
        pages = len(rows)
        emb = b.db.one("SELECT COUNT(*) c FROM wiki_pages WHERE embedding IS NOT NULL")["c"]
        return {
            "pages": pages, "links": len(self._edges(rows)), "embedded": emb,
            "memories": {"total": mem["c"], "embedded": mem["e"] or 0},
            "embeddings": {"mode": b.settings.embeddings, "model": b.settings.embed_model, "available": await b.llm.embeddings_available(), "error": self.embed_error},
            "pending_items": self.pending_items(), "last_ingest": b.db.kv_get("wiki_last_ingest"),
            "lint": b.db.kv_get("wiki_lint"), "dir": str(self.dir),
        }

    # ------------------------------------------------------- background loop
    def _touch(self) -> None:
        now = time.time()
        if not self._dirty:
            self._dirty_since = now
        self._dirty, self._dirty_at = True, now

    def _on_event(self, ev) -> None:
        t, d = ev.type, ev.data
        if t == "goal.update":
            if d.get("parent_id") is not None and d.get("status") in ("done", "failed"):
                ok = d["status"] == "done"
                self._touch()
                self.log("goal", f"#{d['id']} {_plain(d.get('title'), 100)}", f"{'succeeded' if ok else 'failed'}: {_plain(d.get('result'), 300)} → [[episodes/goal-{d['id']}]]",
                         key=f"goal:{d['id']}:{d['status']}")
        elif t == "tool.created":
            self._touch()
            self.log("tool", f"{d.get('name')} ({'tests OK' if d.get('passed') else 'tests failed'})", _plain(d.get("description"), 200), key=f"tool:{d.get('name')}:{d.get('passed')}")
        elif t == "evolution":
            self._touch()
            self.log("evolution", f"{d.get('kind')} {d.get('target')}: {d.get('status')}", _plain(d.get("reason"), 200) + " → [[evolution]]", key=f"evo:{ev.seq}")
        elif t == "goal.main_changed":
            self._touch()
            self.log("goal", "New main goal", _plain(d.get("text"), 300), key=f"main:{ev.seq}")
        elif t == "lesson.learned":
            self._touch()
            if d.get("new"):
                self.log("lesson", _plain(d.get("text"), 100), "→ [[lessons]]", key=f"lesson:{ev.seq}")
        elif t == "status.report":
            self._touch()
            self.log("status", f"Estimated progress {round(d.get('progress', 0))}%", "→ [[status]]", key=f"status:{ev.seq}")
        elif t in ("selfmodel.update", "memory.add"):
            self._touch()

    async def _run(self) -> None:
        b = self.b
        q = b.bus.subscribe()
        try:
            while True:
                try:
                    self._on_event(await asyncio.wait_for(q.get(), timeout=2))
                except asyncio.TimeoutError:
                    pass
                except Exception as e:  # noqa: BLE001 - the wiki must never take the system down
                    await b.bus.publish("system.log", None, level="error", text=f"Wiki evento: {type(e).__name__}: {str(e)[:150]}")
                now = time.time()
                if self._dirty and (now - self._dirty_at >= QUIET_SECONDS or now - self._dirty_since >= MAX_DIRTY_SECONDS):
                    self._dirty = False
                    try:
                        await self.sync_all()
                        await self.embed_pending()
                    except Exception as e:  # noqa: BLE001
                        await b.bus.publish("system.log", None, level="error", text=f"Wiki sync: {type(e).__name__}: {str(e)[:150]}")
                if now - self._last_maint >= 60:
                    self._last_maint = now
                    await self._maybe_ingest()
        finally:
            b.bus.unsubscribe(q)

    async def _maybe_ingest(self) -> None:
        if self.b.control.state != "running" or (self._ingest_task and not self._ingest_task.done()) or time.time() < self._next_ingest_at:
            return
        pending = self.pending_items()
        last = self.b.db.kv_get("wiki_last_ingest") or 0
        if pending >= INGEST_MIN_ITEMS or (pending and time.time() - last >= INGEST_MAX_AGE):
            self._ingest_task = asyncio.create_task(self._ingest_guarded())

    async def _ingest_guarded(self) -> None:
        try:
            await self.ingest()
        except Exception as e:  # noqa: BLE001
            self._next_ingest_at = time.time() + 600
            await self.b.bus.publish("system.log", None, level="error", text=f"Wiki ingest: {type(e).__name__}: {str(e)[:150]}")
