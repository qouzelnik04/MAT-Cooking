"""MAT-Cooking -- recepty a plán vaření pro Home Assistant."""

import os
import re

APP_NAME = "MAT-Cooking"


def _version() -> str:
    """Verze z config.yaml -- jediné místo, kde se verze mění."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "config.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r'^version:\s*"([^"]+)"', fh.read(), re.MULTILINE)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


APP_VERSION = _version()
