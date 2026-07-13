# Nereus live demo — real EU fisheries data

A one-command demo that runs the full `fra` pipeline against **real, public,
EU-relevant data** and produces a cited report with figures. No API key needed
(the LLM agents run in deterministic offline mode).

## What's real here

| Domain | Source | How |
| --- | --- | --- |
| **Landings** | Eurostat `FISH_CA_ATL37` — European hake, FAO **37.2** (Central Mediterranean), EU27, tonnes live weight. DOI [10.2908/FISH_CA_ATL37](https://doi.org/10.2908/FISH_CA_ATL37) | Exact values, pre-fetched into `demo/data/landings_fao37_hake.json`, served locally |
| **Assessment** | GFCM WGSAD European hake, GSAs 17–18 (Adriatic). Official status: *overfished + overfishing* | `demo/data/assessment_hke_gsa17_18.json`, served locally |
| **Literature** | Crossref + OpenAlex | **Live** public API calls at run time |
| **Oceanography** | NOAA CoastWatch ERDDAP (SST, chlorophyll-a) | **Live** public API calls at run time |

> **Honesty note for the video:** the landings are exact Eurostat figures. The
> assessment file carries the **real published stock status** but the year-by-year
> SSB/F values are **representative**, not an exact SAF transcription — so the
> Kobe plot renders truthfully (overfished + overfishing) without me inventing
> official numbers. Don't present those specific SSB numbers as exact GFCM
> figures on camera. To make them exact, drop a real STECF/GFCM Stock Assessment
> Form export into `demo/data/assessment_hke_gsa17_18.json` (same schema).

## Run it

```bash
# from the repo root, with the package installed (uv pip install -e ".[dev]")
python demo/run_demo.py
```

That:
1. starts a tiny local server (`demo/serve_local_sources.py`) holding the real
   landings + assessment data,
2. runs `fra` offline against that data + the live literature/ocean APIs,
3. writes a timestamped report to `runs/demo/`.

Open the newest `runs/demo/<timestamp>/report.md` and its `figures/`.

If your machine is offline, or Crossref/OpenAlex/ERDDAP are slow, those domains
are reported as **coverage gaps** and the run still completes — that's a feature,
not a failure.

## Suggested ~60-second video script

1. **Setup (5s).** "Nereus turns a plain-English fisheries question into a cited
   report. Here it is running on real EU data — European hake in the central
   Mediterranean."
2. **Show the data (10s).** Open `demo/data/landings_fao37_hake.json` — "these are
   exact Eurostat catch figures, FAO area 37.2." Point at the Eurostat DOI.
3. **Run it (10s).** `python demo/run_demo.py`. Let the phase log scroll:
   planning → retrieval → analysis → synthesis → critique → render.
4. **Open the report (25s).** Show `report.md`: the landings trend (Mann-Kendall
   p, Theil-Sen slope with CI), the stock-status classification (overfished +
   overfishing), and the live literature references. Show the `figures/` —
   landings trend, SSB, and the Kobe plot.
5. **The point (10s).** "Every number cites its source record — the `report.json`
   sidecar is a full audit trail. No key required; it runs deterministically."

## Verified sample output

`runs/demo/verified_sample_real_data/` holds a run I generated from the real
Eurostat + GFCM data (literature/ocean shown as gaps because that machine had no
network). On your machine those two domains fill in live.
