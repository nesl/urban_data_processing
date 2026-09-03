import io
import gzip
import json
from pathlib import Path
import queue
import socket
import tarfile
import threading
import time
from zipfile import ZipFile
import pytest

from replay.catalog import DataCatalog, open_file_reference
from replay.engine import JSONLSink, follow, historical
from replay.model import FileReference, Observation
from replay.protocol import SocketJSONLSink, inline_observation


def _require_local_sockets():
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.close()
    except PermissionError:
        pytest.skip("sandbox does not permit local sockets")


def _catalog(tmp_path):
    data = tmp_path / "data"; backup = tmp_path / "backup"
    data.mkdir(); backup.mkdir()
    return data, backup, DataCatalog(data, backup)


def test_historical_replay_reads_local_air_and_orders_by_time(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    day = data / "air_data" / "20260831"; day.mkdir(parents=True)
    (day / "20260831120000.csv").write_text("2,34.2,-118.2,8.5\n", encoding="utf-8")
    (day / "20260831110000.csv").write_text("1,34.1,-118.1,7.5\n", encoding="utf-8")

    rows = list(historical(catalog, ["air"], __import__("datetime").date(2026, 8, 31), __import__("datetime").date(2026, 9, 1)))

    assert [row.sensor for row in rows] == ["1", "2"]
    assert rows[0].data == {"pm25": 7.5}
    assert rows[0].latitude == 34.1


def test_catalog_prefers_local_partition_over_archive(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    local = data / "air_data" / "20260831"; local.mkdir(parents=True)
    (local / "local.csv").write_text("local", encoding="utf-8")
    archive_dir = backup / "raw" / "air_data"; archive_dir.mkdir(parents=True)
    with tarfile.open(archive_dir / "20260831.tar", "w") as archive:
        payload = b"archive"
        info = tarfile.TarInfo("20260831/archive.csv"); info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    partition = catalog.partition("air_data", "20260831")

    assert partition is not None and not partition.archive
    assert [item.name for item in partition.files()] == ["local.csv"]


def test_archive_email_raw_is_converted_without_semantic_enrichment(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    archive_dir = backup / "raw" / "citizen_data"; archive_dir.mkdir(parents=True)
    csv_data = ("schema_version,source,imap_uid,message_id,received_at,sender,subject,body,ingested_at\n"
                "email_raw.v1,citizen,42,<m>,2026-08-01T12:00:00Z,noreply@mail.citizen.com,Alert,Raw body,2026-08-01T12:01:00Z\n").encode()
    with tarfile.open(archive_dir / "20260801.tar", "w") as archive:
        info = tarfile.TarInfo("20260801/1.csv"); info.size = len(csv_data)
        archive.addfile(info, io.BytesIO(csv_data))

    rows = list(historical(catalog, ["citizen"], __import__("datetime").date(2026, 8, 1), __import__("datetime").date(2026, 8, 2)))

    assert len(rows) == 1
    assert rows[0].data == {"sender": "noreply@mail.citizen.com", "subject": "Alert", "body": "Raw body"}
    assert rows[0].raw["member"] == "20260801/1.csv"


def test_legacy_email_fields_are_preserved_not_generated(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    day = data / "twitter_data" / "20260801"; day.mkdir(parents=True)
    (day / "legacy.csv").write_text(
        "email_time,start_time,end_time,location,event,body,author\n"
        "2026-08-01T10:00:00Z,2026-08-01T10:01:00Z,2026-08-01T10:02:00Z,Downtown,Fire,Smoke reported,NotifyLA\n",
        encoding="utf-8",
    )

    row = list(historical(catalog, ["twitter"], __import__("datetime").date(2026, 8, 1), __import__("datetime").date(2026, 8, 2)))[0]

    assert row.data["legacy_event_type"] == "Fire"
    assert row.data["legacy_location"] == "Downtown"
    assert row.raw["format"] == "twitter_email.legacy"


def test_new_gdelt_article_body_is_used_and_no_network_is_needed(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    day = data / "gkg" / "20260805"; article = day / "20260805070000.gkg"
    article.mkdir(parents=True)
    url = "https://example.com/story"
    import hashlib
    article_id = hashlib.sha256(url.encode()).hexdigest()[:24]
    (day / "20260805070000.gkg.csv").write_text(f"0,1,2,3,4\na,20260805070000,c,d,{url}\n", encoding="utf-8")
    (article / f"{article_id}.txt").write_text("Saved article body", encoding="utf-8")
    (article / f"{article_id}.json").write_text(json.dumps({"title": "Saved title", "status": "ok"}), encoding="utf-8")

    row = list(historical(catalog, ["gdelt"], __import__("datetime").date(2026, 8, 5), __import__("datetime").date(2026, 8, 6)))[0]

    assert row.data["body"] == "Saved article body"
    assert row.data["title"] == "Saved title"


def test_legacy_email_rfc_date_is_used_when_event_timestamp_is_na(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    day = data / "citizen_data" / "20260831"; day.mkdir(parents=True)
    (day / "legacy.csv").write_text(
        "Timestamp,Location,Event Name,Event Type,Description,Original Date\n"
        'N/A,Downtown,Fire,fire,Smoke,"Mon, 31 Aug 2026 23:51:23 +0000 (UTC)"\n',
        encoding="utf-8",
    )
    row = list(historical(catalog, ["citizen"], __import__("datetime").date(2026, 8, 31), __import__("datetime").date(2026, 9, 1)))[0]
    assert row.time == "2026-08-31T23:51:23Z"


def test_archived_camera_asset_remains_readable(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    archive_dir = backup / "raw" / "cctv"; archive_dir.mkdir(parents=True)
    image = b"fake-jpeg"
    with tarfile.open(archive_dir / "20260801.tar", "w") as archive:
        info = tarfile.TarInfo("20260801/Camera A/20260801120000.jpg"); info.size = len(image)
        archive.addfile(info, io.BytesIO(image))

    row = list(historical(catalog, ["cctv"], __import__("datetime").date(2026, 8, 1), __import__("datetime").date(2026, 8, 2)))[0]

    assert row.sensor == "Camera A"
    assert row.files[0].member == "20260801/Camera A/20260801120000.jpg"
    with open_file_reference(row.files[0]) as handle:
        assert handle.read() == image


def _write_one_local_example_per_source(data: Path):
    day = "20260801"
    air = data / "air_data" / day; air.mkdir(parents=True)
    (air / "20260801120000.csv").write_text("1,34.1,-118.1,7.5\n", encoding="utf-8")
    weather = data / "weather_data" / day / "Pasadena,  US"; weather.mkdir(parents=True)
    (weather / "20260801120000.csv").write_text("75,clear,40,2.5,270\n", encoding="utf-8")
    for source in ("cctv", "alertcalifornia"):
        camera = data / source / day / "Camera A"; camera.mkdir(parents=True)
        (camera / "20260801120000.jpg").write_bytes(b"jpeg")
        if source == "alertcalifornia":
            (camera / "20260801120000.location").write_text("34.2,-118.2,180", encoding="utf-8")
    for folder, source in (("citizen_data", "citizen"), ("twitter_data", "twitter")):
        email = data / folder / day; email.mkdir(parents=True)
        (email / "1.csv").write_text(
            "schema_version,source,imap_uid,message_id,received_at,sender,subject,body,ingested_at\n"
            f"email_raw.v1,{source},1,<m-{source}>,2026-08-01T12:00:00Z,a@b,Subject,Body,2026-08-01T12:01:00Z\n",
            encoding="utf-8",
        )
    gdelt = data / "gkg" / day; gdelt.mkdir(parents=True)
    (gdelt / "20260801120000.gkg.csv").write_text(
        "0,1,2,3,4\na,20260801120000,c,d,https://example.com/news\n", encoding="utf-8"
    )
    stations = data / "pem_data_station_5min" / day; stations.mkdir(parents=True)
    station_row = ["08/01/2026 12:00:00", "100"] + [""] * 8 + ["0.12", "55"]
    (stations / "station.txt.gz").write_bytes(gzip.compress((",".join(station_row) + "\n").encode()))
    incidents = data / "pem_data_chp_incidents_day" / day; incidents.mkdir(parents=True)
    incident_row = ["incident-1", "", "", "08/01/2026 12:00:00", "Crash"] + [""] * 4 + ["34.3", "-118.3"] + [""] * 7 + ["high", "30"]
    with ZipFile(incidents / "incident.txt.zip", "w") as archive:
        archive.writestr("all_text_chp_incident_day_2026_08_01.txt.gz", gzip.compress((",".join(incident_row) + "\n").encode()))


def test_one_local_instance_of_every_supported_source_reaches_jsonl(tmp_path):
    data, backup, catalog = _catalog(tmp_path)
    _write_one_local_example_per_source(data)
    stream = io.StringIO(); sink = JSONLSink(stream)
    sources = ["air", "weather", "pems-stations", "pems-incidents", "cctv",
               "alertcalifornia", "citizen", "twitter", "gdelt"]
    for row in historical(catalog, sources, __import__("datetime").date(2026, 8, 1), __import__("datetime").date(2026, 8, 2)):
        sink.write(row)
    output = [json.loads(line) for line in stream.getvalue().splitlines()]

    assert {row["source"] for row in output} == set(sources)
    assert all({"id", "source", "time", "sensor", "data", "files", "raw"} <= row.keys() for row in output)
    assert next(row for row in output if row["source"] == "weather")["data"]["wind_direction_degrees"] == 270
    assert next(row for row in output if row["source"] == "cctv")["files"][0]["media_type"] == "image/jpeg"


def test_live_mode_emits_only_data_added_after_baseline(tmp_path, monkeypatch):
    data, backup, catalog = _catalog(tmp_path)
    today = __import__("datetime").date.today(); day_name = today.strftime("%Y%m%d")
    day = data / "air_data" / day_name; day.mkdir(parents=True)
    (day / f"{day_name}090000.csv").write_text("1,34,-118,1\n", encoding="utf-8")
    seen = {row.id for row in historical(catalog, ["air"], today, today + __import__("datetime").timedelta(days=1))}
    (day / f"{day_name}100000.csv").write_text("1,34,-118,2\n", encoding="utf-8")
    stream = io.StringIO(); sink = JSONLSink(stream)
    monkeypatch.setattr("replay.engine.time.sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))

    try:
        follow(catalog, ["air"], sink.write, poll_seconds=0, seen=seen)
    except KeyboardInterrupt:
        pass

    output = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert len(output) == 1
    assert output[0]["data"]["pm25"] == 2


def test_catch_up_then_live_jsonl_has_both_without_duplicate(tmp_path, monkeypatch):
    data, backup, catalog = _catalog(tmp_path)
    today = __import__("datetime").date.today(); day_name = today.strftime("%Y%m%d")
    day = data / "air_data" / day_name; day.mkdir(parents=True)
    (day / f"{day_name}090000.csv").write_text("1,34,-118,1\n", encoding="utf-8")
    stream = io.StringIO(); sink = JSONLSink(stream); seen = set()
    for row in historical(catalog, ["air"], today, today + __import__("datetime").timedelta(days=1)):
        sink.write(row); seen.add(row.id)
    (day / f"{day_name}100000.csv").write_text("1,34,-118,2\n", encoding="utf-8")
    monkeypatch.setattr("replay.engine.time.sleep", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        follow(catalog, ["air"], sink.write, poll_seconds=0, seen=seen)
    except KeyboardInterrupt:
        pass

    output = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert [row["data"]["pm25"] for row in output] == [1, 2]
    assert len({row["id"] for row in output}) == 2


def test_inline_archive_asset_contains_verified_portable_bytes(tmp_path):
    archive_path = tmp_path / "day.tar"; content = b"camera-image-bytes"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("20260801/Camera/image.jpg"); info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    observation = Observation("cctv:1", "cctv", "2026-08-01T12:00:00Z", "Camera", {},
                              files=(FileReference(str(archive_path), "image/jpeg", "20260801/Camera/image.jpg"),))

    wire = inline_observation(observation)

    import base64, hashlib
    assert base64.b64decode(wire["files"][0]["content_base64"]) == content
    assert wire["files"][0]["sha256"] == hashlib.sha256(content).hexdigest()
    assert "path" not in wire["files"][0] and "member" not in wire["files"][0]


def test_socket_sender_waits_for_handler_and_receiver_writes_jsonl(tmp_path):
    _require_local_sockets()
    ports = queue.Queue(); received = queue.Queue(); handled = threading.Event(); release = threading.Event()
    def receive_one():
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0)); server.listen(); ports.put(server.getsockname()[1])
            connection, _ = server.accept()
            with connection:
                reader = connection.makefile("r"); writer = connection.makefile("w")
                value = json.loads(reader.readline()); received.put(value); handled.set(); assert release.wait(2)
                writer.write(json.dumps({"id": value["id"], "accepted": True}) + "\n"); writer.flush()
    server = threading.Thread(target=receive_one, daemon=True); server.start(); port = ports.get(timeout=2)
    observation = Observation("air:1", "air", "2026-08-01T12:00:00Z", "1", {"pm25": 4.2})
    completed = threading.Event()

    def send():
        with SocketJSONLSink("127.0.0.1", port, timeout=2, retries=0) as sink:
            sink.write(observation)
        completed.set()

    sender = threading.Thread(target=send, daemon=True); sender.start()
    assert handled.wait(2)
    assert not completed.is_set()  # sender is blocked until handler completion/ACK
    release.set(); sender.join(2); server.join(2)

    assert completed.is_set()
    value = received.get(timeout=1)
    assert value["id"] == "air:1"
    assert value["data"]["pm25"] == 4.2


def test_socket_round_trip_inlines_local_image(tmp_path):
    _require_local_sockets()
    image = tmp_path / "image.jpg"; image.write_bytes(b"jpeg-content")
    ports = queue.Queue(); received = queue.Queue()
    def receive_one():
        with socket.socket() as server_socket:
            server_socket.bind(("127.0.0.1", 0)); server_socket.listen(); ports.put(server_socket.getsockname()[1])
            connection, _ = server_socket.accept()
            with connection:
                reader = connection.makefile("r"); writer = connection.makefile("w")
                value = json.loads(reader.readline()); received.put(value)
                writer.write(json.dumps({"id": value["id"], "accepted": True}) + "\n"); writer.flush()
    server = threading.Thread(target=receive_one, daemon=True); server.start(); port = ports.get(timeout=2)
    observation = Observation("cctv:2", "cctv", "2026-08-01T12:00:00Z", "Camera", {},
                              files=(FileReference(str(image), "image/jpeg"),))
    with SocketJSONLSink("127.0.0.1", port, timeout=2, retries=0) as sink:
        sink.write(observation)
    server.join(2)

    value = received.get(timeout=1)
    import base64
    assert base64.b64decode(value["files"][0]["content_base64"]) == b"jpeg-content"
