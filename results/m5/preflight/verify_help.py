from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from km_quantum.__main__ import main
from km_quantum.m5.cli import COMMANDS
from km_quantum.m5.io import atomic_json

results = {}
for name in COMMANDS:
    with redirect_stdout(StringIO()):
        try:
            main(["m5", name, "--help"])
        except SystemExit as caught:
            results[name] = caught.code
assert len(results) == 8 and all(code == 0 for code in results.values())
atomic_json(Path("results/m5/preflight/cli_help.json"), {
    "status": "PASS", "commands": results, "hardware_used": False,
})
print(results)
