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
    def reverse_geocode(self, latitude: float, longitude: float) -> Mapping[str, Any] | None: ...


class NoModelBackend:
    """Useful for anomaly-only deployments and tests."""
    def annotate_text(self, text): return {}
    def annotate_image(self, content, media_type): return {}
    def geocode(self, location): return None
    def reverse_geocode(self, latitude, longitude): return None


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
    return "\n\n".join(str(data.get(key) or "").strip() for key in (
        "headline", "title", "subject", "article_text", "body", "description"
    )
                       if str(data.get(key) or "").strip())


def _merge_labels(first, second):
    best = {}
    for item in list(first or []) + list(second or []):
        if not isinstance(item, Mapping) or not item.get("name"): continue
        try:
            score = float(item.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        normalized = {**item, "score": score}
        old = best.get(item["name"])
        if old is None or score > old["score"]:
            best[item["name"]] = normalized
    return sorted(best.values(), key=lambda item: -item["score"])


class Enricher:
    # Version 2 gives news its event-specific incident-label prompt. Bumping the
    # durable-cache namespace prevents older generic labels from being reused.
    VERSION = "5"

    def __init__(self, backend: EnrichmentBackend | None = None, *, detector=None,
                 anomaly_threshold: float = 0.25, article_retriever=None, cache=None):
        self.backend = backend or NoModelBackend()
        self.detector = detector or MultimodalAnomalyDetector(
            use_text_embeddings=False, use_image_clip=True, use_yolo=False, use_river=False)
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

    def _reverse_geocode(self, latitude: float, longitude: float):
        cache_key = f"coordinates:{float(latitude):.6f},{float(longitude):.6f}"
        if self.durable_cache is not None:
            cached = self.durable_cache.get_geocode(cache_key)
            if cached is not None:
                return cached
        resolver = getattr(self.backend, "reverse_geocode", None)
        resolved = resolver(latitude, longitude) if resolver is not None else None
        if resolved and self.durable_cache is not None:
            self.durable_cache.put_geocode(cache_key, dict(resolved))
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
            elif text:
                if value.get("source") in {"gdelt", "news"} and hasattr(self.backend, "annotate_news"):
                    semantic = self.backend.annotate_news(text)
                else:
                    semantic = self.backend.annotate_text(text)
            semantic = dict(semantic or {})
            if "effects" in semantic:
                existing["effects"] = _merge_labels(existing.get("effects"), semantic.pop("effects"))
            semantic_incidents = semantic.pop("incidents", [])
            if image is not None:
                # CLIP is only a cheap gate. For images, the VLM's direct
                # inspection is authoritative about whether an incident is
                # actually visible; an omitted/empty result clears CLIP labels.
                existing["incidents"] = _merge_labels([], semantic_incidents)
            elif semantic_incidents:
                if value.get("source") in {"gdelt", "news"}:
                    # Preserve the detector's generic incident candidates for
                    # IncidentLens and expose event-specific news labels
                    # separately for SIGMUS graph construction.
                    existing["news_incidents"] = _merge_labels([], semantic_incidents)
                else:
                    existing["incidents"] = _merge_labels(existing.get("incidents"), semantic_incidents)
            existing.update(semantic)
            if (image is not None and value.get("latitude") is not None
                    and value.get("longitude") is not None):
                # Sensor metadata wins over a place guessed from pixels,
                # watermarks, or camera-network branding.
                existing.pop("location", None)
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
        # Coordinate normalization is independent of the anomaly gate. This
        # keeps ordinary sensor reports spatially legible without paying for an
        # LLM call; the durable geocode cache bounds repeated provider requests.
        location = existing.get("location")
        location = dict(location) if isinstance(location, Mapping) else {}
        latitude = location.get("latitude", value.get("latitude"))
        longitude = location.get("longitude", value.get("longitude"))
        has_name = location.get("formatted_address") or location.get("text")
        if not has_name and latitude is not None and longitude is not None:
            resolved = self._reverse_geocode(float(latitude), float(longitude))
            if resolved:
                existing["location"] = {**location, **dict(resolved),
                                        "latitude": latitude, "longitude": longitude}
        value["annotations"] = existing
        enriched = Observation.from_dict(value)
        self.cache[key] = enriched
        if self.durable_cache is not None:
            self.durable_cache.put(observation, force, enriched)
        return enriched
