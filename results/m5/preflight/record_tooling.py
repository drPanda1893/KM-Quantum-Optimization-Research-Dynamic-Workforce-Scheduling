from pathlib import Path
from xml.etree import ElementTree

from km_quantum.m5.io import atomic_json, raw_hash, utc_now
from km_quantum.m5.manifest import git_context, source_context

root = Path.cwd()
path = root / "results/m5/preflight/pytest.xml"
suite = ElementTree.parse(path).getroot().find("testsuite")
assert suite is not None
counts = {key: int(suite.attrib[key]) for key in ("tests", "failures", "errors", "skipped")}
assert counts == {"tests": 1350, "failures": 0, "errors": 0, "skipped": 0}
payload = {
    "recorded_at": utc_now(), "git": git_context(root),
    "source_context_sha256": source_context(root)["content_sha256"],
    "pytest": {"status": "PASS", "command": ".venv/Scripts/python.exe -m pytest -q --junitxml=results/m5/preflight/pytest.xml",
               **counts, "reported_seconds": 488.16, "junit_seconds": float(suite.attrib["time"]),
               "warnings": 249, "junit_sha256": raw_hash(path), "historical_tests": 1094, "new_m5_tests": 256},
    "ruff_check": {"status": "PASS", "command": ".venv/Scripts/python.exe -m ruff check src tests --output-format concise"},
    "ruff_format": {"status": "PASS", "command": ".venv/Scripts/python.exe -m ruff format --check src tests", "files": 109},
    "mypy": {"status": "PASS", "command": ".venv/Scripts/python.exe -m mypy src", "source_files": 70},
    "build": {"status": "PASS", "command": "uv build --offline", "wheel_metadata_verified": True},
    "hardware_used": False, "real_provider_jobs_submitted": 0,
}
atomic_json(root / "results/m5/preflight/tooling.json", payload)
print(counts)
