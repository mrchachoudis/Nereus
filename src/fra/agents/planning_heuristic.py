"""A lightweight, deterministic research-question parser.

This is not a substitute for the LLM Planner — it is (a) a grounding aid whose
output is handed to the model as a starting point, and (b) the offline fallback
used when no LLM is available, so the example run and tests work keyless. It
extracts species, FAO area/GSA, and a year range from the question text using
regex + a small alias table, and refuses to invent anything it can't find.
"""

from __future__ import annotations

import re

from fra.models import DataDomain, ResearchPlan, SpatialUnit, SubQuestion, Taxon, TimeRange
from fra.spatial import normalize_area

# common-name -> scientific-name aliases for species we can name confidently
_SPECIES_ALIASES: dict[str, str] = {
    "european hake": "Merluccius merluccius",
    "hake": "Merluccius merluccius",
    "sardine": "Sardina pilchardus",
    "european pilchard": "Sardina pilchardus",
    "anchovy": "Engraulis encrasicolus",
    "bluefin tuna": "Thunnus thynnus",
    "atlantic cod": "Gadus morhua",
    "cod": "Gadus morhua",
    "red mullet": "Mullus barbatus",
}

_YEAR_RANGE_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})\s*[-–—to]{1,3}\s*(1[89]\d{2}|20\d{2})\b")
_FAO_RE = re.compile(r"\bFAO(?:\s+(?:area|major\s+area))?\s+(\d{1,2}(?:\.\d{1,2}){0,2})", re.I)
_GSA_RE = re.compile(r"\bGSA\s*0*(\d{1,2})\b", re.I)
_SCI_NAME_RE = re.compile(r"\b([A-Z][a-z]+)\s+([a-z]{3,})\b")


def _find_taxa(text: str) -> list[Taxon]:
    found: dict[str, Taxon] = {}
    low = text.lower()
    for alias, sci in _SPECIES_ALIASES.items():
        if alias in low:
            found[sci] = Taxon(scientific_name=sci)
    # explicit binomial like "Merluccius merluccius"
    for m in _SCI_NAME_RE.finditer(text):
        genus, species = m.group(1), m.group(2)
        # avoid false positives from sentence-initial words by requiring the
        # second token look like a species epithet (all lower, no vowel-less noise)
        if genus.lower() in {"assess", "european", "fao", "gsa", "stock"}:
            continue
        binomial = f"{genus} {species}"
        if binomial not in found and species not in {"area", "stock", "status"}:
            found[binomial] = Taxon(scientific_name=binomial)
    return list(found.values())


def _find_areas(text: str) -> list[SpatialUnit]:
    areas: list[SpatialUnit] = []
    for m in _GSA_RE.finditer(text):
        try:
            areas.append(normalize_area(f"GSA {m.group(1)}"))
        except ValueError:
            continue
    for m in _FAO_RE.finditer(text):
        try:
            areas.append(normalize_area(f"FAO {m.group(1)}"))
        except ValueError:
            continue
    # de-dup by fao_area
    seen: set[str] = set()
    unique: list[SpatialUnit] = []
    for a in areas:
        if a.fao_area not in seen:
            seen.add(a.fao_area)
            unique.append(a)
    return unique


def _find_time_range(text: str) -> TimeRange | None:
    m = _YEAR_RANGE_RE.search(text)
    if not m:
        return None
    return TimeRange(start_year=int(m.group(1)), end_year=int(m.group(2)))


def _infer_domains(text: str) -> list[DataDomain]:
    low = text.lower()
    domains: list[DataDomain] = []
    if any(w in low for w in ("landing", "catch", "harvest")):
        domains.append("landings")
    if any(w in low for w in ("stock", "status", "assessment", "overfish", "biomass", "ssb")):
        domains.append("assessment")
    if any(
        w in low for w in ("environment", "driver", "sst", "temperature", "chlorophyll", "ocean")
    ):
        domains.append("ocean")
    domains.append("literature")
    # If nothing specific, retrieve the core quantitative domains.
    if set(domains) == {"literature"}:
        domains = ["landings", "assessment", "literature"]
    # preserve order, de-dup
    out: list[DataDomain] = []
    for d in domains:
        if d not in out:
            out.append(d)
    return out


def heuristic_plan(question: str) -> ResearchPlan:
    """Parse ``question`` into a :class:`ResearchPlan`, or request clarification.

    Never fabricates a taxon, area, or year. If any of species/area/time-range is
    missing, returns a clarification-requesting plan listing exactly what's absent.
    """
    taxa = _find_taxa(question)
    areas = _find_areas(question)
    time_range = _find_time_range(question)

    missing: list[str] = []
    if not taxa:
        missing.append("target species (scientific or common name)")
    if not areas:
        missing.append("spatial area (FAO major area code or GFCM GSA)")
    if time_range is None:
        missing.append("time range (start and end year)")

    if missing:
        return ResearchPlan(
            question=question,
            needs_clarification=True,
            clarification_questions=[f"Please specify the {m}." for m in missing],
            rationale="Heuristic parser could not resolve required plan fields.",
        )

    domains = _infer_domains(question)
    sub_questions = [
        SubQuestion(
            id="sq1", text="What is the landings/catch trend over the period?", domains=["landings"]
        ),
        SubQuestion(
            id="sq2",
            text="What is the current stock status vs reference points?",
            domains=["assessment"],
        ),
    ]
    if "ocean" in domains:
        sub_questions.append(
            SubQuestion(
                id="sq3",
                text="Are environmental covariates associated with the stock metrics?",
                domains=["ocean", "assessment"],
            )
        )
    return ResearchPlan(
        question=question,
        taxa=taxa,
        areas=areas,
        time_range=time_range,
        required_domains=domains,
        sub_questions=sub_questions,
        rationale="Parsed deterministically from the question text.",
    )
