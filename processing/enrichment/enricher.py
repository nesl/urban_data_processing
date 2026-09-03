"""One anomaly-gated enrichment implementation shared by both consumers."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
import json
import mimetypes
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Protocol

from urban_observation_model import Observation
from .anomaly import MultimodalAnomalyDetector


class EnrichmentBackend(Protocol):
    def annotate_text(self, text: str) -> Mapping[str, Any]: ...
    def annotate_image(self, content: bytes, media_type: str) -> Mapping[str, Any]: ...
    def geocode(self, location: str) -> Mapping[str, Any] | None: ...


class NoModelBackend:
    """Useful for anomaly-only deployments and tests."""
    def annotate_text(self, text): return {}
    def annotate_image(self, content, media_type): return {}
    def geocode(self, location): return None


def _report(value: Mapping[str, Any], image_path: str | None = None) -> dict[str, Any]:
    data = dict(value.get("data") or {})
    if image_path: data["image_filepath"] = image_path
    return {
        "report_id": value["id"], "report_date": value["time"],
        "sensor_id": value["sensor"], "sensor_name": value["sensor"],
        "sensor_type": value["source"],
        "location": {"latitude": value.get("latitude"), "longitude": value.get("longitude")},
        "data": data,
    }


@contextmanager
def _image_path(observation: Observation):
    image = next((item for item in observation.files if item.media_type.startswith("image/")), None)
    if image is None:
        yield None
        return
    suffix = mimetypes.guess_extension(image.media_type) or Path(image.name).suffix or ".img"
    handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        handle.write(image.content); handle.close()
        yield handle.name
    finally:
        try: os.unlink(handle.name)
        except OSError: pass


def _text(value: Mapping[str, Any]) -> str:
    data = value.get("data") or {}
    return "\n\n".join(str(data.get(key) or "").strip() for key in ("title", "subject", "body", "description")
                       if str(data.get(key) or "").strip())


def _merge_labels(first, second):
    best = {}
    for item in list(first or []) + list(second or []):
        if not isinstance(item, Mapping) or not item.get("name"): continue
        old = best.get(item["name"])
        if old is None or float(item.get("score", 0)) > float(old.get("score", 0)): best[item["name"]] = dict(item)
    return sorted(best.values(), key=lambda item: -float(item.get("score", 0)))


class Enricher:
    VERSION = "1"

    def __init__(self, backend: EnrichmentBackend | None = None, *, detector=None,
                 anomaly_threshold: float = 0.25, article_retriever=None, cache=None):
        self.backend = backend or NoModelBackend()
        self.detector = detector or MultimodalAnomalyDetector(
            use_text_embeddings=False, use_image_clip=False, use_yolo=False, use_river=False)
        self.anomaly_threshold = anomaly_threshold
        self.article_retriever = article_retriever
        self.durable_cache = cache
        self.cache: dict[tuple[str, bool], Observation] = {}

    def _geocode(self, location: str):
        if self.durable_cache is not None:
            cached = self.durable_cache.get_geocode(location)
            if cached is not None:
                return cached
        resolved = self.backend.geocode(location)
        if resolved and self.durable_cache is not None:
            self.durable_cache.put_geocode(location, dict(resolved))
        return resolved

    def enrich(self, observation: Observation, *, force: bool = False) -> Observation:
        # IDs are stable across replay, but content hashing also protects against
        # a corrected source record reusing an earlier ID.
        key = (observation.to_json(), force)
        if key in self.cache: return self.cache[key]
        if self.durable_cache is not None:
            cached = self.durable_cache.get(observation, force)
            if cached is not None:
                self.cache[key] = cached
                return cached
        value = observation.to_dict(); existing = deepcopy(value.get("annotations") or {})
        text = _text(value)
        if not text and value.get("source") == "gdelt" and (value.get("data") or {}).get("url") and self.article_retriever:
            try:
                text = self.article_retriever((value.get("data") or {})["url"])
                existing["article_retrieval"] = {"status": "ok"}
            except Exception as exc:
                existing["article_retrieval"] = {"status": "failed", "error": str(exc)}
        with _image_path(observation) as image_path:
            result = self.detector.detect_report(_report(value, image_path))
        payload = result.to_observation_model_payload()
        score = float((payload.get("anomaly") or {}).get("score", 0.0))
        existing["anomaly"] = {**(payload.get("anomaly") or {}), "is_anomaly": score >= self.anomaly_threshold}
        existing["effects"] = payload.get("observed_effects", [])
        existing["incidents"] = payload.get("possible_incidents", [])
        if not force and score < self.anomaly_threshold:
            existing["enrichment"] = {
                "status": "skipped_by_anomaly", "forced": False, "version": self.VERSION,
            }
        else:
            semantic: Mapping[str, Any] = {}
            image = next((item for item in observation.files if item.media_type.startswith("image/")), None)
            if image is not None: semantic = self.backend.annotate_image(image.content, image.media_type)
            elif text: semantic = self.backend.annotate_text(text)
            semantic = dict(semantic or {})
            for name in ("effects", "incidents"):
                if name in semantic: existing[name] = _merge_labels(existing.get(name), semantic.pop(name))
            existing.update(semantic)
            location = existing.get("location")
            location_text = location.get("text") if isinstance(location, Mapping) else None
            if location_text and not (location.get("latitude") is not None and location.get("longitude") is not None):
                resolved = self._geocode(str(location_text))
                if resolved: existing["location"] = {**location, **dict(resolved)}
            entities = existing.get("entities")
            if isinstance(entities, list):
                for entity in entities:
                    if not isinstance(entity, dict):
                        continue
                    entity_location = entity.get("location")
                    if isinstance(entity_location, str):
                        entity_location = {"text": entity_location}
                    if not isinstance(entity_location, dict):
                        continue
                    if entity_location.get("latitude") is not None and entity_location.get("longitude") is not None:
                        continue
                    if entity_location.get("text"):
                        resolved = self._geocode(str(entity_location["text"]))
                        if resolved:
                            entity["location"] = {**entity_location, **dict(resolved)}
            existing["enrichment"] = {
                "status": "completed",
                "forced": force,
                "version": self.VERSION,
                "provider": getattr(self.backend, "provider", "none"),
                "model": (
                    getattr(self.backend, "vision_model", None)
                    if image is not None else getattr(self.backend, "text_model", None)
                ),
            }
        value["annotations"] = existing
        enriched = Observation.from_dict(value)
        self.cache[key] = enriched
        if self.durable_cache is not None:
            self.durable_cache.put(observation, force, enriched)
        return enriched
