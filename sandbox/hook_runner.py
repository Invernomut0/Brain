"""Runs inside the sandbox: executes an evolvable hook function.

Usage: python /runner/hook_runner.py <hook> <function>   (JSON {"args": [...]} on stdin)
"""
import importlib.util
import json
import sys
import traceback

MARK = "__BRAIN_RESULT__"

hook, func = sys.argv[1], sys.argv[2]
payload = json.loads(sys.stdin.read() or "{}")
try:
    spec = importlib.util.spec_from_file_location(hook, f"/evolvable/hooks/{hook}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = {"ok": True, "result": getattr(mod, func)(*payload.get("args", []))}
except Exception:  # noqa: BLE001
    out = {"ok": False, "error": traceback.format_exc()[-1200:]}
print(MARK + json.dumps(out, default=str))
