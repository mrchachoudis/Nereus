# Nereus demo — real EU fisheries data

A one-command demo that runs the full `fra` pipeline against **real, public,
EU-relevant data** and produces a cited report with figures. No API key required:
the LLM agents run in deterministic offline mode.

## What's real here

| Domain | Source | How |
| --- | --- | --- |
| **Landings** | Eurostat `FISH_CA_ATL37` — European hake, FAO **37.2** (Central Mediterranean), EU27, tonnes live weight. DOI [10.2908/FISH_CA_ATL37](https://doi.org/10.2908/FISH_CA_ATL37) | Exact values, pre-fetched into `demo/data/landings_fao37_hake.json`, served locally |
| **Assessment** | GFCM WGSAD European hake, GSAs 17–18 (Adriatic). Official status: *overfished + overfishing* | `demo/data/assessment_hke_gsa17_18.json`, served locally |
| **Literature** | Crossref + OpenAlex | Live public API calls at run time |
| **Oceanography** | NOAA CoastWatch ERDDAP (SST, chlorophyll-a) | Live public API calls at run time |

## Note on provenance

The landings are exact Eurostat figures. The assessment file carries the **real
published stock status** (overfished + subject to overfishing), but its
year-by-year SSB/F values are **representative**, not an exact Stock Assessment
Form (SAF) transcription — enough to exercise the status classifier and the Kobe
plot without asserting unofficial numbers. For a production run, replace
`demo/data/assessment_hke_gsa17_18.json` with an official STECF/GFCM SAF export in
the same schema.

## Run it

```bash
# from the repo root, with the package installed (uv pip install -e ".[dev]")
python demo/run_demo.py
```

This:

1. starts a small local server (`demo/serve_local_sources.py`) holding the real
   landings + assessment data,
2. runs `fra` offline against that data plus the live literature/ocean APIs,
3. writes a timestamped report to `runs/demo/`.

Open the newest `runs/demo/<timestamp>/report.md` and its `figures/` (landings
trend, SSB, and the Kobe stock-status plot). Every quantitative claim in the
report cites the source record it came from; `report.json` is the full audit
trail.

If the machine is offline, or Crossref/OpenAlex/ERDDAP are slow or unreachable,
those domains are reported as **coverage gaps** and the run still completes.
