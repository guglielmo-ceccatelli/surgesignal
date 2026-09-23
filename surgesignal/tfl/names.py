"""Station-name normalisation so TfL's free text matches TfL's own station list.

TfL writes the same station several ways: "King's Cross St. Pancras" / "Kings Cross St Pancras",
"Elephant & Castle" / "Elephant and Castle", "Edgware Road (Circle Line)" / "Edgware Road",
"London Paddington" / "Paddington". Each station is indexed under every variant (lookup_keys),
and free text is normalised the same way before lookup.
"""

from __future__ import annotations

import re

SUFFIXES = (" underground station", " rail station", " dlr station", " station", "-underground")


def normalise(text: str) -> str:
    s = text.lower().replace("’", "'")
    for suffix in SUFFIXES:
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    s = s.replace("&", " and ").replace("'", "")
    s = re.sub(r"\bst\.", "st", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def lookup_keys(name: str) -> set[str]:
    """Every normalised form a station may appear under in TfL free text."""
    base = name
    for suffix in (" Underground Station", " Rail Station", " DLR Station", "-Underground"):
        base = base.replace(suffix, "")
    variants = {base, re.sub(r"\s*\([^)]*\)", "", base)}
    variants |= {v[len("London "):] for v in list(variants) if v.startswith("London ")}
    return {k for k in (normalise(v) for v in variants) if k}


def display_name(name: str) -> str:
    """Station name for alerts: no suffixes, no 'London ' prefix, keeps disambiguating brackets."""
    for suffix in (" Underground Station", " Rail Station", " DLR Station", "-Underground"):
        name = name.replace(suffix, "")
    return name[len("London "):] if name.startswith("London ") else name
