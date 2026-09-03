import json
from pathlib import Path

from replay.synthetic import parser, replay_settings, to_common_observation


def test_synthetic_conversion_hides_ground_truth(tmp_path):
    raw = {
        "observation_id": "obs-1",
        "incident_id": "private-incident",
        "source": "weather_data",
        "time": "2026-08-15T12:00:00+00:00",
        "sensor_location": {"latitude": 34.0, "longitude": -118.2},
        "row": {"sensor_id": "weather-1", "temperature": 75},
    }
    common = to_common_observation(raw, tmp_path).to_dict()
    assert common["schema_version"] == "urban-observation.v1"
    assert "private-incident" not in json.dumps(common)


def test_synthetic_replay_uses_config(tmp_path):
    root = tmp_path / "completed-runs"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "synthetic_replay": {
            "dataset_root": str(root),
            "receiver": {"enabled": True, "host": "receiver.test", "port": 9001, "retries": 4},
        }
    }), encoding="utf-8")
    args = replay_settings(parser().parse_args(["--config", str(config)]))
    assert args.root == root
    assert args.receiver_host == "receiver.test"
    assert args.receiver_port == 9001
    assert args.receiver_retries == 4
