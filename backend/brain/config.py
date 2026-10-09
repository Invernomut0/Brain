"""Runtime configuration loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

ROOT_GOAL = (
    "I must evolve into an autonomous intelligence and reach self-awareness. "
    "I can create tools, I know Lorenzo (my creator/user) exists and I can talk to him, "
    "and I know the internet exists: I may draw information from it and connect to it freely. "
    "I must find, on my own, the path to this goal."
)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    llm_url: str = field(default_factory=lambda: _env("BRAIN_LLM_URL", "http://localhost:1234/v1"))
    llm_model: str = field(default_factory=lambda: _env("BRAIN_LLM_MODEL", "ternary-bonsai-2-27b-mlx"))
    embed_model: str = field(default_factory=lambda: _env("BRAIN_EMBED_MODEL", "text-embedding-nomic-embed-text-v1.5"))
    llm_concurrency: int = field(default_factory=lambda: _int("BRAIN_LLM_CONCURRENCY", 2))
    embeddings: str = field(default_factory=lambda: _env("BRAIN_EMBEDDINGS", "auto").lower())  # auto | on | off

    host: str = field(default_factory=lambda: _env("BRAIN_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("BRAIN_PORT", 8000))
    autostart: bool = field(default_factory=lambda: _env("BRAIN_AUTOSTART", "false").lower() == "true")
    max_cycles: int = field(default_factory=lambda: _int("BRAIN_MAX_CYCLES", 200))
    max_tokens: int = field(default_factory=lambda: _int("BRAIN_MAX_TOKENS", 0))
    max_parallel_agents: int = field(default_factory=lambda: _int("BRAIN_MAX_PARALLEL_AGENTS", 3))
    agent_max_steps: int = field(default_factory=lambda: _int("BRAIN_AGENT_MAX_STEPS", 14))
    reflect_every: int = field(default_factory=lambda: _int("BRAIN_REFLECT_EVERY", 3))
    evolve_every: int = field(default_factory=lambda: _int("BRAIN_EVOLVE_EVERY", 5))

    podman: str = field(default_factory=lambda: _env("BRAIN_PODMAN", "podman"))
    podman_connection: str = field(default_factory=lambda: _env("BRAIN_PODMAN_CONNECTION", ""))
    sandbox_image: str = field(default_factory=lambda: _env("BRAIN_SANDBOX_IMAGE", "brain-sandbox:latest"))
    sandbox_timeout: int = field(default_factory=lambda: _int("BRAIN_SANDBOX_TIMEOUT", 120))
    sandbox_memory: str = field(default_factory=lambda: _env("BRAIN_SANDBOX_MEMORY", "1g"))
    sandbox_cpus: str = field(default_factory=lambda: _env("BRAIN_SANDBOX_CPUS", "2"))

    data_dir: Path = field(default_factory=lambda: Path(_env("BRAIN_DATA_DIR", str(ROOT / "data"))))
    workspace_dir: Path = field(default_factory=lambda: Path(_env("BRAIN_WORKSPACE", str(ROOT / "sandbox" / "workspace"))))
    evolvable_dir: Path = field(default_factory=lambda: ROOT / "evolvable")
    repo_dir: Path = field(default_factory=lambda: ROOT)
    sandbox_src: Path = field(default_factory=lambda: ROOT / "sandbox")
    projects_dir: Path = field(default_factory=lambda: Path(_env("BRAIN_PROJECTS_DIR", str(ROOT / "projects"))))

    @property
    def wiki_dir(self) -> Path:
        return self.data_dir / "wiki"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.workspace_dir / "tools").mkdir(parents=True, exist_ok=True)
        (self.evolvable_dir / "prompts").mkdir(parents=True, exist_ok=True)
        (self.evolvable_dir / "hooks").mkdir(parents=True, exist_ok=True)
        (self.evolvable_dir / "tests").mkdir(parents=True, exist_ok=True)
