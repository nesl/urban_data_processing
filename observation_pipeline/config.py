from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def get_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    resolved = Path(path or os.environ.get("OBSERVATION_PIPELINE_CONFIG", "./config.json"))
    with resolved.open(encoding="utf-8") as stream:
        return json.load(stream)

