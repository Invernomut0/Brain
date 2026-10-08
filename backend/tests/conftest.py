"""Test fixtures. Tests use the REAL LM Studio, Podman sandbox and internet (no mocks)."""
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from brain.config import ROOT, Settings  # noqa: E402
from brain.core import Brain  # noqa: E402


@pytest.fixture
def settings(tmp_path_factory) -> Settings:
    # Sandbox mounts must live under $HOME so the Podman VM can see them.
    base = ROOT / "data" / "test-runs"
    base.mkdir(parents=True, exist_ok=True)
    root = Path(tmp_path_factory.mktemp("run", numbered=True).name)
    root = base / root.name
    shutil.rmtree(root, ignore_errors=True)
    (root / "repo").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root / "repo", check=True)
    s = Settings()
    s.data_dir = root / "data"
    s.workspace_dir = root / "workspace"
    s.repo_dir = root / "repo"
    s.evolvable_dir = root / "repo" / "evolvable"
    yield s
    shutil.rmtree(root, ignore_errors=True)


@pytest_asyncio.fixture
async def brain(settings):
    b = Brain(settings)
    await b.evolution.seed()
    yield b
    await b.sandbox.close()
    await b.llm.close()
