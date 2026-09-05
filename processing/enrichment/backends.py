"""Optional external services used only after the anomaly gate passes."""

from __future__ import annotations

import base64
import json
import os
from typing import Any


def _object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start: raise ValueError("model did not return a JSON object")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict): raise ValueError("model response is not an object")
    return value


class OpenAIBackend:
    """Factual text/image annotations with optional Google geocoding."""
    def __init__(self, *, api_key=None, text_model="gpt-4o-mini", vision_model="gpt-4o-mini",
                 base_url=None, google_api_key=None):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"), base_url=base_url)
        self.text_model, self.vision_model = text_model, vision_model
        self.provider = "openai"
        self.google_api_key = google_api_key or os.environ.get("GOOGLE_PLACES_API_KEY")

    @staticmethod
    def prompt():
        return ("Return one factual JSON object that completes all ordinary enrichment in one call. "
                "Allowed keys: summary, event, location, entities, relations, effects, incidents. "
                "event may contain name, type, start_time, end_time, and description. location may "
                "contain only text. entities are arrays of {name,type,description,location}. relations "
                "are arrays of {subject,predicate,object}. effects/incidents are arrays of {name,score}. "
                "Use null or omit a field when unsupported. Do not invent facts.")

    @classmethod
    def news_prompt(cls):
        return (
            cls.prompt()
            + " For a news article, incidents must describe specific real-world event instances, "
              "not generic categories. Use a concise incident name that distinguishes the event "
              "with its supported event kind and place, plus a date or year when the article "
              "supports one (for example, '2026 Los Angeles Downtown Warehouse Fire'). The event "
              "type may remain generic. Do not create an incident when the article does not "
              "support a particular event."
        )

    def annotate_text(self, text):
        response = self.client.chat.completions.create(model=self.text_model, temperature=0,
            messages=[{"role": "system", "content": self.prompt()}, {"role": "user", "content": text}])
        return _object(response.choices[0].message.content or "")

    def annotate_news(self, text):
        response = self.client.chat.completions.create(model=self.text_model, temperature=0,
            messages=[{"role": "system", "content": self.news_prompt()},
                      {"role": "user", "content": text}])
        return _object(response.choices[0].message.content or "")

    def annotate_image(self, content, media_type):
        url = f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"
        response = self.client.chat.completions.create(model=self.vision_model, temperature=0,
            messages=[{"role": "user", "content": [{"type": "text", "text": self.prompt()},
                       {"type": "image_url", "image_url": {"url": url}}]}])
        return _object(response.choices[0].message.content or "")

    def geocode(self, location):
        if not self.google_api_key: return None
        import requests
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                                params={"address": location, "key": self.google_api_key}, timeout=20)
        response.raise_for_status(); results = response.json().get("results") or []
        if not results: return None
        result = results[0]; point = result["geometry"]["location"]
        return {"formatted_address": result.get("formatted_address"), "latitude": point["lat"],
                "longitude": point["lng"], "provider": "google", "provider_place_id": result.get("place_id")}

    def reverse_geocode(self, latitude, longitude):
        if not self.google_api_key: return None
        import requests
        response = requests.get("https://maps.googleapis.com/maps/api/geocode/json",
                                params={"latlng": f"{latitude},{longitude}", "key": self.google_api_key},
                                timeout=20)
        response.raise_for_status(); results = response.json().get("results") or []
        if not results: return None
        result = results[0]; point = result["geometry"]["location"]
        return {"formatted_address": result.get("formatted_address"), "latitude": point["lat"],
                "longitude": point["lng"], "provider": "google", "provider_place_id": result.get("place_id")}


def retrieve_article(url: str) -> str:
    import requests
    from bs4 import BeautifulSoup
    response = requests.get(url, headers={"User-Agent": "urban-observation-enrichment/0.1"}, timeout=20)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for item in soup(["script", "style", "nav", "footer", "header"]): item.decompose()
    text = "\n".join(part.strip() for part in soup.get_text("\n").splitlines() if part.strip())
    if not text: raise ValueError("article contained no readable text")
    return text
