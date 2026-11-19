"""Content topic filters for the shorts workflow."""

from __future__ import annotations

import re

GTA_KEYWORDS = (
    "gta",
    "gta 6",
    "gta6",
    "gta vi",
    "gta-6",
    "gta-vi",
    "gta 5",
    "gta5",
    "gta v",
    "grand theft auto",
    "gta rp",
    "gtav",
    "gtaonline",
)


def is_gta_content(title: str, description: str = "") -> bool:
    """Check if the title or description is GTA/GTA6 related."""
    content = f"{title} {description}".lower()
    tokens = set(re.findall(r"\b[a-zA-Z0-9_-]+\b", content))
    if any(k in tokens for k in ("gta", "gtav", "gta5", "gta6", "gtaonline")):
        return True
    if "grand" in tokens and "theft" in tokens and "auto" in tokens:
        return True
    return any(kw in content for kw in GTA_KEYWORDS)
