"""
Name utilities: enforce "First L" format and fuzzy-match against existing players.
"""

import re
from rapidfuzz import fuzz, process


def normalize_name(raw: str) -> str:
    """
    Attempt to normalize a raw string to "First L" format.
    Examples:
      "alex good"    → "Alex G"
      "ALEX G"       → "Alex G"
      "  Alex  G  "  → "Alex G"
    Returns the raw input (title-cased) if it can't be normalized cleanly.
    """
    parts = raw.strip().split()
    if len(parts) == 0:
        return raw
    if len(parts) == 1:
        return parts[0].capitalize()
    # Take first word as first name, last word's first letter as initial
    first = parts[0].capitalize()
    initial = parts[-1][0].upper()
    return f"{first} {initial}"


def validate_name_format(name: str) -> tuple[bool, str]:
    """
    Returns (is_valid, error_message).
    Valid: exactly "Word L" — one capitalized word, one uppercase letter.
    """
    parts = name.strip().split()
    if len(parts) != 2:
        return False, "Use format 'First L' — e.g. 'Alex G'"
    first, initial = parts
    if not re.fullmatch(r"[A-Za-z]+", first):
        return False, "First name must contain only letters"
    if not re.fullmatch(r"[A-Za-z]", initial):
        return False, "Last initial must be a single letter"
    return True, ""


def find_similar_names(
    name: str,
    existing: list[str],
    threshold: int = 72,
    limit: int = 4,
) -> list[str]:
    """
    Return up to `limit` existing names similar to `name` (score >= threshold).
    Returns [] if existing is empty or no matches above threshold.
    """
    if not existing or not name.strip():
        return []
    results = process.extract(name, existing, scorer=fuzz.WRatio, limit=limit)
    return [r[0] for r in results if r[1] >= threshold and r[0].lower() != name.lower()]
