"""Python in the sandbox only runs inside a virtual environment (real Podman, real PyPI)."""
import pytest

from brain.agents import Agent
from brain.tools import ToolContext

pytestmark = pytest.mark.asyncio

SIX_TOOL = "import six\n\n\ndef run(n: int):\n    return {'double': n * 2, 'six': six.__version__}\n"
SIX_TEST = "from six_tool import run\n\n\ndef test_double():\n    assert run(4)['double'] == 8\n"


def ctx(brain):
    return ToolContext(brain, Agent(brain, "engineer", "t"))


async def call(brain, tool, **args):
    return await brain.tools.call(ctx(brain), tool, args)


async def test_python_exec_is_refused_without_a_venv(brain):
    out = await call(brain, "python_exec", code="print(1)")
    assert out.startswith("ERROR") and "create_venv" in out
    out = await call(brain, "shell_exec", command="python -c 'print(1)'")
    assert out.startswith("ERROR") and "create_venv" in out
    out = await call(brain, "shell_exec", command="pip install six")
    assert out.startswith("ERROR")


async def test_shell_without_python_still_works_before_the_venv_exists(brain):
    out = await call(brain, "shell_exec", command="echo hello")
    assert "hello" in out and "exit=0" in out


async def test_create_venv_then_code_runs_inside_it_with_installed_packages(brain):
    msg = await call(brain, "create_venv", packages=["six"])
    assert msg.startswith("venv 'default' ready"), msg
    out = await call(brain, "python_exec", code="import sys, six; print(sys.prefix, six.__version__)")
    assert "exit=0" in out and "/opt/venvs/default" in out, out
    # the venv is isolated: the system interpreter of the image does not have the package
    system = await brain.sandbox.python("import six")
    assert not system.ok
    # idempotent, and the image's preinstalled packages stay visible
    assert (await call(brain, "create_venv")).startswith("venv 'default' ready")
    assert "exit=0" in await call(brain, "python_exec", code="import numpy, httpx")


async def test_pip_only_works_inside_the_venv(brain):
    await call(brain, "create_venv")
    ok = await call(brain, "shell_exec", command="pip install -q six && python -c 'import six; print(six.__file__)'")
    assert "exit=0" in ok and "/opt/venvs/default" in ok, ok
    # the system pip is refused even when called by absolute path
    sys_pip = await call(brain, "shell_exec", command="/usr/local/bin/pip install -q idna-ssl")
    assert "exit=0" not in sys_pip and "virtualenv" in sys_pip.lower(), sys_pip


async def test_named_venvs_are_separate(brain):
    await call(brain, "create_venv", name="data", packages=["six"])
    await call(brain, "create_venv", name="other")
    assert "exit=0" in await call(brain, "python_exec", code="import six", venv="data")
    assert "exit=0" not in await call(brain, "python_exec", code="import six", venv="other")
    missing = await call(brain, "python_exec", code="print(1)", venv="ghost")
    assert missing.startswith("ERROR") and "ghost" in missing


@pytest.mark.parametrize("name", ["../etc", "Bad Name", "", "a" * 40, "-x"])
async def test_invalid_venv_names_are_rejected(brain, name):
    assert (await call(brain, "create_venv", name=name)).startswith("ERROR")


@pytest.mark.parametrize("spec", ["--index-url=http://evil", "git+https://x/y.git", "six; rm -rf /", "-r reqs.txt", "a b"])
async def test_invalid_package_specs_are_rejected(brain, spec):
    out = await call(brain, "create_venv", packages=[spec])
    assert out.startswith("ERROR") and "invalid package" in out


async def test_agent_made_tools_use_the_default_venv(brain):
    await call(brain, "create_venv", packages=["six"])
    r = await call(brain, "create_tool", name="six_tool", description="doubles", params={"n": "int"}, code=SIX_TOOL, test_code=SIX_TEST)
    assert r["ok"], r
    assert (await call(brain, "six_tool", n=4))["double"] == 8
