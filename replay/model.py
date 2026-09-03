"""The deliberately small, deterministic replay interchange model."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any


@dataclass(frozen=True)
class FileReference:
    path: str
    media_type: str
    member: str | None = None


@dataclass(frozen=True)
class Observation:
    id: str
    source: str
    time: str
    sensor: str
    data: dict[str, Any]
    end_time: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    files: tuple[FileReference, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["files"] = [asdict(item) for item in self.files]
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))
