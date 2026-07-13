"""WoRMS (World Register of Marine Species) taxonomy resolver with a disk cache.

Resolves a scientific name to a :class:`Taxon` carrying a WoRMS AphiaID. Network
lookups are optional: a small built-in table covers common assessed species so
tests and offline runs resolve without hitting the network, and results are
cached on disk so repeat runs are cheap and reproducible.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from fra.models import Taxon

logger = logging.getLogger(__name__)

# WoRMS REST base. Documented default; verify terms of use before heavy use.
WORMS_BASE = "https://www.marinespecies.org/rest"

# Offline seed table for frequently-assessed species. Keyed by lowercase name.
_SEED: dict[str, Taxon] = {
    "merluccius merluccius": Taxon(
        scientific_name="Merluccius merluccius", aphia_id=126484, common_name="European hake"
    ),
    "sardina pilchardus": Taxon(
        scientific_name="Sardina pilchardus", aphia_id=126421, common_name="European pilchard"
    ),
    "engraulis encrasicolus": Taxon(
        scientific_name="Engraulis encrasicolus", aphia_id=126426, common_name="European anchovy"
    ),
    "thunnus thynnus": Taxon(
        scientific_name="Thunnus thynnus", aphia_id=127029, common_name="Atlantic bluefin tuna"
    ),
    "gadus morhua": Taxon(
        scientific_name="Gadus morhua", aphia_id=126436, common_name="Atlantic cod"
    ),
    "mullus barbatus": Taxon(
        scientific_name="Mullus barbatus", aphia_id=126983, common_name="Red mullet"
    ),
}


class TaxonomyResolver:
    """Resolve scientific names to :class:`Taxon` with layered caching.

    Lookup order: in-memory cache -> disk cache -> seed table -> WoRMS REST
    (only if ``allow_network``). An unresolvable name still yields a valid
    :class:`Taxon` with ``aphia_id=None`` - we never block the pipeline on a
    taxonomy miss, but we do log it.
    """

    def __init__(
        self,
        cache_dir: str | Path = ".fra_cache",
        *,
        allow_network: bool = True,
        client: httpx.Client | None = None,
    ) -> None:
        self._cache_path = Path(cache_dir) / "taxonomy.json"
        self._allow_network = allow_network
        self._client = client
        self._mem: dict[str, Taxon] = {}
        self._load_disk()

    def _load_disk(self) -> None:
        if self._cache_path.exists():
            try:
                raw = json.loads(self._cache_path.read_text(encoding="utf-8"))
                self._mem = {k: Taxon.model_validate(v) for k, v in raw.items()}
            except (json.JSONDecodeError, ValueError) as exc:
                logger.warning("taxonomy cache unreadable (%s); starting fresh", exc)
                self._mem = {}

    def _flush_disk(self) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        serializable = {k: v.model_dump() for k, v in self._mem.items()}
        self._cache_path.write_text(
            json.dumps(serializable, indent=2, sort_keys=True), encoding="utf-8"
        )

    def resolve(self, scientific_name: str) -> Taxon:
        key = scientific_name.strip().lower()
        if key in self._mem:
            return self._mem[key]
        if key in _SEED:
            resolved = _SEED[key]
            self._mem[key] = resolved
            self._flush_disk()
            return resolved

        fetched = self._resolve_network(scientific_name) if self._allow_network else None
        if fetched is None:
            logger.info("taxonomy: could not resolve %r to an AphiaID", scientific_name)
            resolved = Taxon(scientific_name=scientific_name.strip(), aphia_id=None)
        else:
            resolved = fetched

        self._mem[key] = resolved
        self._flush_disk()
        return resolved

    def _resolve_network(self, scientific_name: str) -> Taxon | None:
        client = self._client or httpx.Client(timeout=20.0)
        close = self._client is None
        try:
            url = f"{WORMS_BASE}/AphiaRecordsByName/{scientific_name}"
            resp = client.get(url, params={"like": "false", "marine_only": "true"})
            if resp.status_code == 204:  # WoRMS returns 204 for no match
                return None
            resp.raise_for_status()
            records = resp.json()
            if not records:
                return None
            best = records[0]
            return Taxon(
                scientific_name=best.get("scientificname", scientific_name),
                aphia_id=best.get("AphiaID"),
                common_name=best.get("valid_name") if best.get("valid_name") else None,
            )
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
            logger.warning("taxonomy network lookup failed for %r: %s", scientific_name, exc)
            return None
        finally:
            if close:
                client.close()
