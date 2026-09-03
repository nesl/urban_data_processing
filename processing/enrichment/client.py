"""Client for the Urban Observations enrichment service."""

import json
from urllib import request

from urban_observation_model import Observation


class EnrichmentClient:
    def __init__(self, url="http://127.0.0.1:8770", *, timeout=180.0):
        self.url, self.timeout = url.rstrip("/"), timeout

    def enrich(self, observation: Observation, *, force=False) -> Observation:
        target = self.url + "/enrich" + ("?force=true" if force else "")
        req = request.Request(target, data=observation.to_json().encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
        with request.urlopen(req, timeout=self.timeout) as response:
            return Observation.from_json(response.read().decode())
