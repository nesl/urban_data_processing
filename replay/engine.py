"""Maximum-speed historical replay and polling-based live replay."""

from __future__ import annotations

from datetime import date, timedelta
import time
from typing import Callable, Iterable, Iterator

from .catalog import DataCatalog
from .model import Observation
from .readers import SOURCE_FOLDERS, observations


class JSONLSink:
    """Write one complete, inline common observation JSON object per line."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, observation: Observation) -> None:
        from .protocol import inline_observation
        import json
        self.stream.write(json.dumps(inline_observation(observation), ensure_ascii=False, separators=(",", ":")) + "\n")
        self.stream.flush()


def historical(catalog: DataCatalog, sources: list[str], start: date, end: date, **options) -> Iterator[Observation]:
    """Yield a fixed half-open date interval in global event-time order."""
    folders = [SOURCE_FOLDERS[source] for source in sources]
    for day in catalog.select_dates(folders, start, end):
        records = []
        for source in sources:
            folder = SOURCE_FOLDERS[source]
            partition = catalog.partition(folder, day)
            if partition:
                records.extend(observations(partition, source, **options))
        yield from sorted(records, key=lambda item: (item.time, item.source, item.id))


def follow(catalog: DataCatalog, sources: list[str], emit: Callable[[Observation], None], *,
           poll_seconds: float = 5.0, seen: set[str] | None = None, **options) -> None:
    """Poll already-collected files and emit newly observed IDs until interrupted.

    State is intentionally process-local in this first version. Restarting a
    monitor replays the current local date unless the consumer deduplicates IDs.
    """
    known = seen if seen is not None else set()
    while True:
        today = date.today(); start = today - timedelta(days=1); end = today + timedelta(days=1)
        pending = [item for item in historical(catalog, sources, start, end, **options) if item.id not in known]
        for item in pending:
            emit(item); known.add(item.id)
        time.sleep(poll_seconds)
