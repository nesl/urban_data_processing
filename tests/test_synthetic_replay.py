import json
from pathlib import Path

from replay.replay import parser as real_replay_parser
from replay.synthetic import observation_count, parser, replay_settings, to_common_observation


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


def test_synthetic_progress_count_ignores_blank_lines_and_honors_limit(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "observations.txt").write_text("{}\n\n{}\n", encoding="utf-8")
    (second / "observations.txt").write_text("{}\n", encoding="utf-8")

    assert observation_count([first, second]) == 3
    assert observation_count([first, second], limit=2) == 2


def test_synthetic_replay_uses_minimal_config_and_builtin_defaults(tmp_path):
    root = tmp_path / "completed-runs"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "replay": {
            "receiver": {"host": "receiver.test"},
        }
    }), encoding="utf-8")
    args = replay_settings(parser().parse_args([str(root), "--config", str(config)]))
    assert args.root == root
    assert args.receiver_host == "receiver.test"
    assert args.receiver_port == 8766
    assert args.receiver_timeout == 120.0
    assert args.receiver_retries == 3
    assert args.recursive is True
    assert args.interval_seconds == 0.0
    assert args.output is None
    assert args.mapping_output is None
    assert args.receiver is True


def test_synthetic_replay_accepts_legacy_config_overrides(tmp_path):
    root = tmp_path / "completed-runs"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "synthetic_replay": {
            "dataset_root": str(root),
            "receiver": {
                "enabled": True,
                "host": "receiver.test",
                "port": 9001,
                "retries": 4,
            },
        }
    }), encoding="utf-8")
    args = replay_settings(parser().parse_args(["--config", str(config)]))
    assert args.root == root
    assert args.receiver_host == "receiver.test"
    assert args.receiver_port == 9001
    assert args.receiver_retries == 4


def test_real_and_synthetic_replay_share_receiver_cli_names():
    options = [
        "--receiver-host", "receiver.test",
        "--receiver-port", "9001",
        "--receiver-timeout", "15",
        "--receiver-retries", "2",
        "--no-receiver",
    ]
    real = real_replay_parser().parse_args(options)
    synthetic = parser().parse_args(options)
    for args in (real, synthetic):
        assert args.receiver_host == "receiver.test"
        assert args.receiver_port == 9001
        assert args.receiver_timeout == 15.0
        assert args.receiver_retries == 2
    assert real.no_receiver is True
    assert synthetic.receiver is False


def test_real_replay_accepts_legacy_receiver_cli_aliases():
    args = real_replay_parser().parse_args([
        "--socket-host", "receiver.test",
        "--socket-port", "9001",
        "--ack-timeout", "15",
        "--network-retries", "2",
        "--no-socket",
    ])
    assert args.receiver_host == "receiver.test"
    assert args.receiver_port == 9001
    assert args.receiver_timeout == 15.0
    assert args.receiver_retries == 2
    assert args.no_receiver is True
