"""Personal settings, read from .env so that no personal detail lives in the code.

Everything that identifies the candidate, the name and contact line on the CV, the
signature on an application mail, the home city the report marks, is read from here.
The repository then holds only code, and .env stays on the machine it was written on.

.env.example lists every key with a made-up value.
"""

from __future__ import annotations

import os
import pathlib

ENV_FILE = pathlib.Path(__file__).resolve().parent / ".env"


def load() -> dict[str, str]:
    """The environment, overlaid on .env, so a variable set in the shell wins over the file."""
    values: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    values.update({k: v for k, v in os.environ.items() if k.startswith(("CANDIDATE_", "GMAIL_", "HOME_"))})
    return values


def get(key: str, default: str = "") -> str:
    return load().get(key, default)


def candidate_name() -> str:
    return get("CANDIDATE_NAME", "Your Name")


def contact_parts() -> list[str]:
    """Phone, email and profile links, in the order the CV prints them, skipping any left unset."""
    keys = ("CANDIDATE_PHONE", "CANDIDATE_EMAIL", "CANDIDATE_LINKEDIN", "CANDIDATE_GITHUB")
    return [value for value in (get(k) for k in keys) if value]


def home_places() -> list[str]:
    """Spellings of the home city, which the report marks on a job's card."""
    return [p.strip().lower() for p in get("HOME_CITY").split(",") if p.strip()]
