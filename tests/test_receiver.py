import hashlib
import io
import json
import socket
import threading

from urban_observation_model import InlineFile, Observation, SCHEMA_VERSION
from processing.receiver.receiver import EnrichingHandler, JSONLHandler, handle_connection


class FlushableStringIO(io.StringIO):
    def fileno(self):
        raise OSError


def test_receiver_validates_inline_model_and_acks_after_handler():
    content = b"image"
    file = InlineFile("x.jpg", "image/jpeg", len(content), hashlib.sha256(content).hexdigest(), content)
    value = {"schema_version": SCHEMA_VERSION, "id": "cctv:1", "source": "cctv",
             "time": "2026-09-02T12:00:00Z", "sensor": "Camera", "data": {}, "files": [file.to_dict()]}
    left, right = socket.socketpair(); output = io.StringIO()
    class Handler:
        def handle(self, observation): output.write(observation.to_json() + "\n")
    thread = threading.Thread(target=handle_connection, args=(left, Handler()), daemon=True); thread.start()
    reader = right.makefile("r"); writer = right.makefile("w")
    writer.write(json.dumps(value) + "\n"); writer.flush()
    response = json.loads(reader.readline())
    right.close(); thread.join(2)
    assert response == {"id": "cctv:1", "accepted": True}
    assert json.loads(output.getvalue())["files"][0]["sha256"] == file.sha256


def test_receiver_handler_calls_shared_enrichment_before_common_sink():
    seen = []
    class Client:
        def enrich(self, observation, force=False):
            value = observation.to_dict(); value["annotations"] = {"enrichment": {"status": "completed"}}
            return Observation.from_dict(value)
    class Sink:
        def handle(self, observation): seen.append(observation)
    value = {"schema_version": SCHEMA_VERSION, "id": "air:1", "source": "air",
             "time": "2026-09-02T12:00:00Z", "sensor": "one", "data": {}, "files": []}
    EnrichingHandler(Sink(), Client()).handle(Observation.from_dict(value))
    assert seen[0].value["annotations"]["enrichment"]["status"] == "completed"
