"""Runs inside the sandbox container: loads a tool module and executes it.

Usage: python /runner/tool_runner.py <tool_name>   (JSON args on stdin)
Contract: /workspace/tools/<name>.py exposes run(**kwargs) -> JSON-serialisable.
"""
import importlib.util
import json
import sys
import traceback

MARK = "__BRAIN_RESULT__"


def main() -> int:
    name = sys.argv[1]
    args = json.loads(sys.stdin.read() or "{}")
    try:
        spec = importlib.util.spec_from_file_location(name, f"/workspace/tools/{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        out = {"ok": True, "result": mod.run(**args)}
    except Exception:  # noqa: BLE001
        out = {"ok": False, "error": traceback.format_exc()[-1500:]}
    print(MARK + json.dumps(out, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
