"""FAO major-fishing-area and GFCM GSA normalization.

Pure, offline, dependency-free. Connectors call :func:`normalize_area` to coerce
whatever spatial coding a source uses into a canonical :class:`SpatialUnit`.
"""

from __future__ import annotations

import re

from fra.models import SpatialUnit

# Human labels for FAO major areas (top-level). Sub-areas inherit and extend.
_FAO_MAJOR_LABELS: dict[str, str] = {
    "18": "Arctic Sea",
    "21": "Atlantic, Northwest",
    "27": "Atlantic, Northeast",
    "31": "Atlantic, Western Central",
    "34": "Atlantic, Eastern Central",
    "37": "Mediterranean and Black Sea",
    "41": "Atlantic, Southwest",
    "47": "Atlantic, Southeast",
    "48": "Atlantic, Antarctic",
    "51": "Indian Ocean, Western",
    "57": "Indian Ocean, Eastern",
    "58": "Indian Ocean, Antarctic and Southern",
    "61": "Pacific, Northwest",
    "67": "Pacific, Northeast",
    "71": "Pacific, Western Central",
    "77": "Pacific, Eastern Central",
    "81": "Pacific, Southwest",
    "87": "Pacific, Southeast",
    "88": "Pacific, Antarctic",
}

# GFCM Geographical Sub-Areas map into FAO 37 sub-areas. This is a partial,
# commonly-used mapping; extend as needed. Key = GSA number (as string).
_GSA_TO_FAO_SUBAREA: dict[str, str] = {
    "1": "37.1.1",  # Northern Alboran Sea
    "5": "37.1.1",  # Balearic Islands
    "6": "37.1.1",  # Northern Spain
    "7": "37.1.2",  # Gulf of Lions
    "9": "37.1.3",  # Ligurian & N. Tyrrhenian Sea
    "10": "37.1.3",  # S. & Central Tyrrhenian Sea
    "17": "37.2.1",  # Northern Adriatic
    "18": "37.2.1",  # Southern Adriatic
    "19": "37.2.2",  # Western Ionian Sea
    "20": "37.2.2",  # Eastern Ionian Sea
    "22": "37.3.1",  # Aegean Sea
    "23": "37.3.1",  # Crete
    "24": "37.3.1",  # North Levant
    "29": "37.4.2",  # Black Sea
}

_FAO_CODE_RE = re.compile(r"(\d{1,2}(?:\.\d{1,2}){0,2})")
_GSA_RE = re.compile(r"gsa[\s_-]*0*(\d{1,2})", re.IGNORECASE)


def label_for_fao_area(fao_area: str) -> str:
    """Best-effort human label for a (possibly nested) FAO area code."""
    major = fao_area.split(".")[0]
    base = _FAO_MAJOR_LABELS.get(major, f"FAO Area {major}")
    if "." in fao_area:
        return f"{base} (subarea {fao_area})"
    return base


def normalize_area(
    raw: str,
    *,
    gsa: str | None = None,
    label: str | None = None,
) -> SpatialUnit:
    """Coerce a source's spatial string into a canonical :class:`SpatialUnit`.

    Accepts, in priority order:
      * an explicit ``gsa`` argument or a ``"GSA 17"``-style token in ``raw``,
        which is mapped to its FAO sub-area;
      * a bare FAO code embedded anywhere in ``raw`` (e.g. ``"FAO 37.2.1"``).

    Raises ``ValueError`` if nothing recognizable is found — fail loud at ingest.
    """
    gsa_num = gsa
    if gsa_num is None:
        m = _GSA_RE.search(raw)
        if m:
            gsa_num = m.group(1)

    if gsa_num is not None:
        gsa_num = re.sub(r"\D", "", gsa_num)
        fao = _GSA_TO_FAO_SUBAREA.get(gsa_num)
        if fao is None:
            raise ValueError(f"unknown GFCM GSA {gsa_num!r} (no FAO sub-area mapping)")
        return SpatialUnit(
            fao_area=fao,
            gsa=gsa_num,
            label=label or f"GSA {gsa_num} — {label_for_fao_area(fao)}",
        )

    m = _FAO_CODE_RE.search(raw)
    if not m:
        raise ValueError(f"no FAO area or GSA code found in {raw!r}")
    fao = m.group(1)
    return SpatialUnit(fao_area=fao, gsa=None, label=label or label_for_fao_area(fao))


def area_contains(container: SpatialUnit, candidate: SpatialUnit) -> bool:
    """True if ``candidate`` is within (or equal to) ``container``.

    Uses the dotted-prefix nesting of FAO codes: ``37`` contains ``37.2`` which
    contains ``37.2.1``.
    """
    c = container.fao_area.split(".")
    k = candidate.fao_area.split(".")
    if len(c) > len(k):
        return False
    return k[: len(c)] == c
