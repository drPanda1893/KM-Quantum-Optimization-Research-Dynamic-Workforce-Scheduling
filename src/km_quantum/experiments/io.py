"""Small, explicit JSON file helpers for the research workflow."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def read_json(path: str | Path) -> Any:
    """Read UTF-8 JSON (also accept a Windows UTF-8 BOM)."""
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def read_model[ModelT: BaseModel](path: str | Path, model: type[ModelT]) -> ModelT:
    return model.model_validate(read_json(path))


def write_json(path: str | Path, value: BaseModel | dict[str, Any]) -> None:
    """Write a human-readable file, creating its output directory if needed."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    target.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def canonical_hash(value: BaseModel | dict[str, Any]) -> str:
    """SHA-256 over sorted, compact JSON; independent of file indentation."""
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    encoded = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
