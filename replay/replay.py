"""Command-line interface for read-only Urban Observations replay."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys

from observation_pipeline.config import get_config
from .catalog import DataCatalog
from .engine import JSONLSink, follow, historical
from .protocol import SocketJSONLSink
from .readers import READERS


def _day(value: str) -> date:
    try: return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc: raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Convert and replay already-collected observations as JSONL")
    result.add_argument("--config", help="configuration file (default: ./config.json)")
    result.add_argument("--from", dest="start", help="inclusive YYYY-MM-DD, or 'now' with --follow")
    result.add_argument("--to", type=_day, help="exclusive YYYY-MM-DD; omit only with --follow")
    result.add_argument("--follow", action="store_true", help="continue monitoring collected files after catch-up")
    result.add_argument("--source", action="append", choices=sorted(READERS), help="repeat to select sources; default: all")
    result.add_argument("--data-root", help="override config save_folder")
    result.add_argument("--backup-root", help="override config backup_folder")
    result.add_argument("--weather-locations", help="OpenWeather coordinate inventory")
    result.add_argument("--cctv-locations", help="Caltrans CCTV KML inventory")
    result.add_argument("--poll-seconds", type=float, default=5.0)
    result.add_argument("--timezone", default="America/Los_Angeles", help="timezone for naive collector/PeMS timestamps")
    result.add_argument("--socket-host", help="send inline observations to a stop-and-wait receiver instead of stdout")
    result.add_argument("--no-socket", action="store_true", help="write JSONL to stdout instead of the configured receiver")
    result.add_argument("--socket-port", type=int)
    result.add_argument("--ack-timeout", type=float, default=120.0)
    result.add_argument("--network-retries", type=int, default=3)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if not args.start: parser().error("--from is required")
    if not args.follow and args.to is None: parser().error("--to is required unless --follow is used")
    if args.start == "now" and not args.follow: parser().error("--from now requires --follow")
    config = get_config(args.config); paths = config.get("paths", {}); replay = config.get("real_replay", {})
    data_root = args.data_root or paths["data_root"]
    backup_root = args.backup_root or paths["backup_root"]
    repository = Path(__file__).parents[1]
    assets = repository / "observation_pipeline" / "assets"
    weather_locations = args.weather_locations or str(assets / "owm_locations.txt")
    cctv_locations = args.cctv_locations or str(assets / "cctv.kml")
    catalog = DataCatalog(data_root, backup_root); sources = args.source or sorted(READERS)
    seen: set[str] = set()
    socket_host = None if args.no_socket else (args.socket_host or replay.get("receiver_host"))
    socket_port = args.socket_port or int(replay.get("receiver_port", 8766))
    sink = (SocketJSONLSink(socket_host, socket_port, timeout=args.ack_timeout, retries=args.network_retries)
            if socket_host else JSONLSink(sys.stdout))
    emit = sink.write
    try:
        if args.start != "now":
            start = _day(args.start); end = args.to or (date.today() + timedelta(days=1))
            for item in historical(catalog, sources, start, end, weather_locations=weather_locations,
                                   cctv_locations=cctv_locations, local_timezone=args.timezone):
                emit(item); seen.add(item.id)
        else:
            # Monitoring means records appearing after startup, not replaying today's
            # already-collected files. This baseline is deliberately not persisted.
            for item in historical(catalog, sources, date.today() - timedelta(days=1), date.today() + timedelta(days=1),
                                   weather_locations=weather_locations, cctv_locations=cctv_locations,
                                   local_timezone=args.timezone):
                seen.add(item.id)
        if args.follow:
            try: follow(catalog, sources, emit, poll_seconds=args.poll_seconds, seen=seen,
                        weather_locations=weather_locations, cctv_locations=cctv_locations,
                        local_timezone=args.timezone)
            except KeyboardInterrupt: return 0
        return 0
    finally:
        close = getattr(sink, "close", None)
        if close is not None: close()


if __name__ == "__main__":
    raise SystemExit(main())
