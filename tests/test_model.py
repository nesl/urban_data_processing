import hashlib

import pytest

from urban_observation_model import InlineFile, Observation, ObservationValidationError, SCHEMA_VERSION


def test_observation_round_trip_and_asset_validation():
    content = b"image"
    file = InlineFile("image.jpg", "image/jpeg", len(content), hashlib.sha256(content).hexdigest(), content)
    value = {"schema_version": SCHEMA_VERSION, "id": "cctv:1", "source": "cctv",
             "time": "2026-09-02T12:00:00Z", "sensor": "Camera", "data": {},
             "files": [file.to_dict()]}
    parsed = Observation.from_json(Observation.from_dict(value).to_json())
    assert parsed.id == "cctv:1"
    assert parsed.files[0].content == content


def test_bad_checksum_is_rejected():
    value = {"schema_version": SCHEMA_VERSION, "id": "cctv:1", "source": "cctv",
             "time": "2026-09-02T12:00:00Z", "sensor": "Camera", "data": {},
             "files": [{"name": "x", "media_type": "x", "size": 1, "sha256": "0" * 64,
                        "content_base64": "eA=="}]}
    with pytest.raises(ObservationValidationError, match="checksum"):
        Observation.from_dict(value)


def test_annotations_are_optional_but_must_be_one_object():
    value = {"schema_version": SCHEMA_VERSION, "id": "air:1", "source": "air",
             "time": "2026-09-02T12:00:00Z", "sensor": "one", "data": {}, "files": [],
             "annotations": {"anomaly": {"score": 0.9}}}
    assert Observation.from_dict(value).value["annotations"]["anomaly"]["score"] == 0.9
    value["annotations"] = []
    with pytest.raises(ObservationValidationError, match="annotations"):
        Observation.from_dict(value)
