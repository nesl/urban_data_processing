"""Dependency-free validation for the single shared observation model."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = "urban-observation.v1"


class ObservationValidationError(ValueError):
    pass


@dataclass(frozen=True)
class InlineFile:
    name: str
    media_type: str
    size: int
    sha256: str
    content: bytes

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "InlineFile":
        try:
            content = base64.b64decode(value["content_base64"], validate=True)
            result = cls(str(value["name"]), str(value["media_type"]), int(value["size"]),
                         str(value["sha256"]), content)
        except (KeyError, TypeError, ValueError) as exc:
            raise ObservationValidationError(f"invalid inline file: {exc}") from exc
        if len(result.content) != result.size:
            raise ObservationValidationError(f"file size mismatch for {result.name}")
        if hashlib.sha256(result.content).hexdigest() != result.sha256:
            raise ObservationValidationError(f"file checksum mismatch for {result.name}")
        return result

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "media_type": self.media_type, "size": self.size,
                "sha256": self.sha256, "content_base64": base64.b64encode(self.content).decode("ascii")}


@dataclass(frozen=True)
class Observation:
    value: dict[str, Any]
    files: tuple[InlineFile, ...]

    @property
    def id(self) -> str:
        return self.value["id"]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Observation":
        if not isinstance(value, Mapping):
            raise ObservationValidationError("observation must be an object")
        data = dict(value)
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ObservationValidationError(f"unsupported schema_version: {data.get('schema_version')!r}")
        for field in ("id", "source", "time", "sensor"):
            if not isinstance(data.get(field), str) or (field != "sensor" and not data[field]):
                raise ObservationValidationError(f"{field} must be a string" + ("" if field == "sensor" else " and nonempty"))
        if not isinstance(data.get("data"), dict) or not isinstance(data.get("files"), list):
            raise ObservationValidationError("data must be an object and files must be an array")
        if "annotations" in data and not isinstance(data["annotations"], dict):
            raise ObservationValidationError("annotations must be an object when present")
        for field, low, high in (("latitude", -90, 90), ("longitude", -180, 180)):
            item = data.get(field)
            if item is not None and (not isinstance(item, (int, float)) or not low <= item <= high):
                raise ObservationValidationError(f"{field} must be null or between {low} and {high}")
        decoded = tuple(InlineFile.from_dict(item) for item in data["files"])
        return cls(data, decoded)

    @classmethod
    def from_json(cls, line: str) -> "Observation":
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ObservationValidationError(f"invalid JSON: {exc}") from exc
        return cls.from_dict(value)

    def to_dict(self) -> dict[str, Any]:
        value = dict(self.value)
        value["files"] = [item.to_dict() for item in self.files]
        return value

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


def schema_path():
    return Path(__file__).with_name("observation-v1.schema.json")
