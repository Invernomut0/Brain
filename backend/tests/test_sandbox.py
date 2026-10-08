import pytest

pytestmark = pytest.mark.asyncio


async def test_sandbox_status_and_python(brain):
    st = await brain.sandbox.status()
    assert st["ok"], st
    r = await brain.sandbox.python("import sys; print(sum(range(10)), sys.version_info.major)")
    assert r.ok and r.stdout.split() == ["45", "3"]


async def test_sandbox_timeout_is_enforced(brain):
    r = await brain.sandbox.python("import time; time.sleep(60)", timeout=3)
    assert not r.ok


async def test_sandbox_has_internet_and_workspace_persists(brain):
    r = await brain.sandbox.python(
        "import httpx, pathlib; pathlib.Path('/workspace/x.txt').write_text('hi'); "
        "print(httpx.get('https://example.com', timeout=20).status_code)"
    )
    assert "200" in r.stdout, r.brief()
    assert (brain.settings.workspace_dir / "x.txt").read_text() == "hi"


async def test_kill_all_stops_running_containers(brain):
    import asyncio

    task = asyncio.create_task(brain.sandbox.python("import time; time.sleep(120)", timeout=100))
    await asyncio.sleep(6)
    await brain.sandbox.kill_all()
    res = await asyncio.wait_for(task, 30)
    assert not res.ok
