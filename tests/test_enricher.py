import hashlib

from urban_observation_model import InlineFile, Observation, SCHEMA_VERSION
from processing.enrichment import Enricher
from processing.enrichment.cache import EnrichmentCache


class Result:
    def __init__(self, score): self.score = score
    def to_observation_model_payload(self):
        return {"anomaly": {"score": self.score, "modality": "test", "diagnostics": {}},
                "observed_effects": [], "possible_incidents": []}


class Detector:
    def __init__(self, score): self.score, self.reports = score, []
    def detect_report(self, report): self.reports.append(report); return Result(self.score)


class Backend:
    def __init__(self):
        self.text_calls = []; self.news_calls = []; self.image_calls = []; self.locations = []
    def annotate_text(self, value):
        self.text_calls.append(value)
        return {"summary": "annotated", "location": {"text": "Los Angeles"},
                "incidents": [{"name": "fire", "score": .9}]}
    def annotate_news(self, value):
        self.news_calls.append(value)
        return {"summary": "news annotated", "location": {"text": "Los Angeles"},
                "incidents": [{"name": "2026 Downtown Los Angeles Fire", "score": .9}]}
    def annotate_image(self, content, media_type):
        self.image_calls.append((content, media_type)); return {"summary": "image annotated"}
    def geocode(self, location):
        self.locations.append(location); return {"latitude": 34.05, "longitude": -118.24}


def observation(source="citizen", data=None, files=None, observation_id="one"):
    return Observation.from_dict({"schema_version": SCHEMA_VERSION, "id": observation_id,
        "source": source, "time": "2026-09-02T12:00:00Z", "sensor": "sensor",
        "data": data or {}, "files": files or []})


def test_low_anomaly_skips_expensive_text_annotation():
    backend = Backend()
    result = Enricher(backend, detector=Detector(.1)).enrich(
        observation(data={"body": "ordinary update"}))
    assert backend.text_calls == []
    assert result.value["annotations"]["enrichment"]["status"] == "skipped_by_anomaly"


def test_high_anomaly_uses_text_model_and_geocoder():
    backend = Backend()
    result = Enricher(backend, detector=Detector(.8)).enrich(
        observation(data={"subject": "Fire reported downtown"}))
    annotations = result.value["annotations"]
    assert backend.text_calls == ["Fire reported downtown"]
    assert annotations["summary"] == "annotated"
    assert annotations["incidents"] == [{"name": "fire", "score": .9}]
    assert annotations["location"]["latitude"] == 34.05


def test_high_anomaly_sends_inline_image_to_vision_backend():
    content = b"jpeg bytes"
    inline = InlineFile("camera.jpg", "image/jpeg", len(content),
                        hashlib.sha256(content).hexdigest(), content)
    backend = Backend()
    result = Enricher(backend, detector=Detector(.8)).enrich(
        observation(source="cctv", files=[inline.to_dict()]))
    assert backend.image_calls == [(content, "image/jpeg")]
    assert result.value["annotations"]["summary"] == "image annotated"


def test_link_only_news_is_retrieved_before_detection_and_annotation():
    backend, detector = Backend(), Detector(.8)
    result = Enricher(backend, detector=detector,
                      article_retriever=lambda url: "Downloaded article body").enrich(
        observation(source="gdelt", data={"url": "https://example.test/story"}))
    assert backend.text_calls == []
    assert backend.news_calls == ["Downloaded article body"]
    assert result.value["annotations"]["incidents"] == []
    assert result.value["annotations"]["news_incidents"] == [
        {"name": "2026 Downtown Los Angeles Fire", "score": .9}
    ]
    assert result.value["annotations"]["article_retrieval"]["status"] == "ok"
    assert detector.reports[0]["sensor_type"] == "gdelt"


def test_force_bypasses_low_anomaly_gate():
    backend = Backend()
    result = Enricher(backend, detector=Detector(0)).enrich(
        observation(data={"body": "manual hypothesis"}), force=True)
    assert backend.text_calls == ["manual hypothesis"]
    enrichment = result.value["annotations"]["enrichment"]
    assert enrichment["status"] == "completed"
    assert enrichment["forced"] is True
    assert enrichment["version"] == Enricher.VERSION


def test_durable_cache_avoids_repeating_model_call_after_restart(tmp_path):
    item = observation(data={"body": "Fire reported"}, observation_id="cached")
    first_backend = Backend()
    first = Enricher(first_backend, detector=Detector(.8),
                     cache=EnrichmentCache(tmp_path / "cache.sqlite3", version=Enricher.VERSION))
    first.enrich(item)
    assert len(first_backend.text_calls) == 1

    second_backend = Backend()
    second = Enricher(second_backend, detector=Detector(.8),
                      cache=EnrichmentCache(tmp_path / "cache.sqlite3", version=Enricher.VERSION))
    result = second.enrich(item)
    assert second_backend.text_calls == []
    assert result.value["annotations"]["summary"] == "annotated"


def test_geocoding_is_reused_across_different_observations(tmp_path):
    cache_path = tmp_path / "cache.sqlite3"
    first_backend = Backend()
    Enricher(first_backend, detector=Detector(.8),
             cache=EnrichmentCache(cache_path, version=Enricher.VERSION)).enrich(
        observation(data={"body": "first fire"}, observation_id="first"))
    assert first_backend.locations == ["Los Angeles"]

    second_backend = Backend()
    Enricher(second_backend, detector=Detector(.8),
             cache=EnrichmentCache(cache_path, version=Enricher.VERSION)).enrich(
        observation(data={"body": "second fire"}, observation_id="second"))
    assert second_backend.text_calls == ["second fire"]
    assert second_backend.locations == []
