import json
import subprocess
from pathlib import Path

from km_quantum.m5.io import atomic_json, utc_now

root = Path.cwd()
python = str(root / ".venv/Scripts/python.exe")
steps = []
commands = [
    ("verify-local", [], 0),
    ("plan", [], 0),
    ("collect", [], 0),
    ("analyze", [], 0),
    ("submit-next", ["--approval", "results/m5/approval_request.json"], 1),
    ("acceptance", [], 0),
]
for name, extra, expected in commands:
    args = [python, "-m", "km_quantum", "m5", name, *extra]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, encoding="utf-8")
    value = json.loads(result.stdout)
    assert result.returncode == expected, (name, value, result.stderr)
    atomic_json(root / "results/m5/preflight" / f"cli_{name}.json", {
        "command": [".venv/Scripts/python.exe", *args[1:]],
        "exit_code": result.returncode, "output": value,
    })
    steps.append({"command": name, "exit_code": result.returncode,
                  "status": value.get("status", value.get("analysis_status"))})
    print(steps[-1], flush=True)
assert steps[-1]["status"] == "OFFLINE_READY"
atomic_json(root / "results/m5/preflight/cli_chain.json", {
    "status": "PASS", "recorded_at": utc_now(), "steps": steps,
    "submission_without_approval_refused": True, "hardware_used": False,
})
