"""Durable content-addressed cache for paid enrichment work."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from urban_observation_model import Observation


class EnrichmentCache:
    def __init__(self, path: str | Path, *, version: str):
        self.path, self.version = Path(path), str(version)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS enrichment_cache "
            "(cache_key TEXT PRIMARY KEY, observation_json TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS geocode_cache "
            "(location_key TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
        )
        self.connection.commit()
        self.lock = threading.Lock()

    def key(self, observation: Observation, force: bool) -> str:
        value = observation.to_dict()
        value.pop("annotations", None)
        canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"{self.version}:{int(force)}:{digest}"

    def get(self, observation: Observation, force: bool) -> Observation | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT observation_json FROM enrichment_cache WHERE cache_key = ?",
                (self.key(observation, force),),
            ).fetchone()
        return Observation.from_json(row[0]) if row else None

    def put(self, source: Observation, force: bool, result: Observation) -> None:
        with self.lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO enrichment_cache(cache_key, observation_json) VALUES (?, ?)",
                (self.key(source, force), result.to_json()),
            )
            self.connection.commit()

    def get_geocode(self, location: str) -> dict | None:
        key = " ".join(location.lower().split())
        with self.lock:
            row = self.connection.execute(
                "SELECT result_json FROM geocode_cache WHERE location_key = ?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put_geocode(self, location: str, result: dict) -> None:
        key = " ".join(location.lower().split())
        with self.lock:
            self.connection.execute(
                "INSERT OR REPLACE INTO geocode_cache(location_key, result_json) VALUES (?, ?)",
                (key, json.dumps(result, ensure_ascii=False, separators=(",", ":"))),
            )
            self.connection.commit()
