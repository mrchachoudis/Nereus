# Fisheries Research Agents (`fra`)

This is a multi-agent system that collects, reconciles, analyzes, and synthesizes
fisheries landings, stock assessments, oceanographic covariates, and primary
literature into a research-grade report. You get figures, quantified uncertainty,
an explicit methods section, and full citations, and every numeric claim traces
back to a source record.

You ask a question:

> *"Assess the stock status and environmental drivers of European hake in FAO
> Area 37.2 over 2010–2023."*

and you get back `report.md`, a machine-readable `report.json` audit sidecar, a
`figures/` directory, and a `run_log.jsonl`, all produced by a team of specialized
agents that an orchestrator coordinates.

Have a look at [`examples/hake_area_37.md`](examples/hake_area_37.md) for a real
sample output that's committed to the repo.

## What this is, and what it isn't

It's a pipeline that goes retrieval, then reconciliation, then analysis, then
synthesis. It retrieves and interprets published assessment outputs (ICES, GFCM,
RAM style) and runs lightweight, well-understood derived analyses such as trend
tests, Kobe-quadrant status classification, and covariate association, all with
quantified uncertainty.

It is not a stock-assessment model. It doesn't implement a statistical
catch-at-age model, and it doesn't fabricate data. Where a source returns nothing,
the coverage gap is reported as a gap and never filled with a guess. No number
reaches the report without a citation to the record it came from.

## Architecture

```
User question
     │
     ▼
[Planner] ──► ResearchPlan ──► (if under-specified) ──► clarification request
     │
     ▼  (plan approved)
[Orchestrator dispatches, in parallel where independent:]
     ├─► [Data Retrieval]  ─► LandingsRecord[], AssessmentRecord[]
     ├─► [Oceanography]     ─► CovariateSeries[]
     └─► [Literature]       ─► Reference[]
     │
     ▼  (all retrieval complete, written to the Blackboard)
[Analysis] ─► AnalysisResult[]   (trends, status classification, associations)
     │
     ▼
[Synthesis] ─► DraftReport
     │
     ▼
[Critic] ──(revise + notes)──► [Synthesis]   ← loop ≤ max_revision_rounds
     │
     ▼  (pass)
FinalReport (report.md + figures/ + report.json sidecar)
```

Agents only communicate through the orchestrator, by way of the shared
*Blackboard*. It's a star topology rather than a mesh. The retrieval agents run
concurrently with `asyncio.gather`, Analysis is a barrier that waits for all
retrieval to finish, and the Synthesis and Critic exchange is a bounded revision
loop.

### Agent roster

| Agent | Responsibility |
|---|---|
| **Planner** | Breaks the question down into a typed `ResearchPlan` covering species, area, time range, domains, and sub-questions. Asks for clarification when the question is under-specified. |
| **Data Retrieval** | Fetches landings and stock-assessment records, then normalizes units, taxonomy (WoRMS AphiaID), and spatial codes (FAO areas and GFCM GSAs). |
| **Oceanography** | Fetches environmental covariates like SST and chlorophyll-a for the plan's space-time box, then aggregates gridded data to annual resolution. |
| **Analysis** | The deterministic core. Runs Mann-Kendall plus Theil-Sen trends, Kobe stock-status classification, and lagged covariate association. It reports effect sizes, p-values, and confidence intervals rather than bare point estimates. |
| **Literature** | Retrieves and ranks primary literature from Crossref and OpenAlex, and de-duplicates by DOI. |
| **Synthesis** | Composes the cited report and enforces the grounding rule, so every quantitative sentence carries a citation to a real record or analysis ID. |
| **Critic** | Adversarially reviews the draft. It mechanically verifies that every citation resolves and every quantitative claim is cited, and adds an LLM review that looks for overconfidence and missing uncertainty. It loops with Synthesis up to the cap. |

## Quickstart

You'll need Python 3.10 or newer. I'd recommend [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Install (editable, with dev tooling)
uv venv
uv pip install -e ".[dev]"

# 2. (Optional) configure keys. The pipeline runs WITHOUT any key in offline mode
cp .env.example .env      # then edit; set ANTHROPIC_API_KEY for the real LLM

# 3. Run the committed example, fully offline, no keys, no network:
fra run "Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2020" \
    --config config/connectors.sample.yaml \
    --offline \
    --out ./runs/
```

That writes a timestamped run directory holding `report.md`, `report.json`,
`figures/`, and `run_log.jsonl`.

### Running against real sources

```bash
export ANTHROPIC_API_KEY=sk-ant-...
fra run "Assess stock status and environmental drivers of European hake in FAO 37.2, 2010-2023" \
    --config config/connectors.yaml \
    --out ./runs/ \
    --max-revisions 3
```

With `ANTHROPIC_API_KEY` set and no `--offline`, the Planner, Synthesis, and Critic
use the Anthropic Messages API. You can configure the model via `FRA_LLM_MODEL` or
`config/connectors.yaml → llm.model`.

Without a key, or with `--offline`, a deterministic offline backend drives those
agents by rule from the same Blackboard context, so the system stays fully runnable
and reproducible with no credentials. The grounding contract holds identically
either way.

Each connector is toggled in `config/connectors.yaml`. If a connector is disabled,
or its required key is missing, it's skipped and its data domain is reported as a
coverage gap rather than failing the run.

## Offline / deterministic mode

The deterministic backend (`fra/offline.py`) is not a language model. It reads the
machine-readable context each agent embeds in its prompt and emits valid,
schema-checked JSON by rule, drawing every number only from the supplied artifacts.
This is what makes the example report and the end-to-end test reproducible without
an API key, and it doubles as a useful reference implementation of the grounding
contract.

## Data sources and terms of use

Endpoint bases live in `config/connectors.yaml` rather than in code, because
sources change and config shouldn't require a code edit. Before you rely on any
source in production, verify its current API base and terms of use:

- **FAO and regional landings portals.** Confirm the query API and licensing for
  your region. The shipped `fao_landings` connector consumes a documented JSON
  schema that you point at your export.
- **RAM Legacy Stock Assessment Database.** CC-BY, so cite the release DOI.
- **ERDDAP / Copernicus Marine.** Confirm the dataset IDs exist on your chosen
  server, and respect each provider's access terms.
- **Crossref / OpenAlex.** Keyless. Send a `mailto` for the polite pool.

Never commit secrets. Keys are read from the environment, and `.env.example`
documents them.

Adding a regional source is a one-file change. Implement the `Connector` protocol
in `src/fra/connectors/` and register it in `connectors/__init__.py`.

## Design decisions

**Star, not mesh.** Agents never call each other. They read and write the shared
`Blackboard` and get dispatched by the orchestrator. This keeps the topology
debuggable, makes the data flow explicit, and structurally prevents the infinite
agent-to-agent loops that a mesh invites.

**The Blackboard.** A single typed pydantic object is the source of truth for a
run. Every external write goes through `Blackboard.record`, which appends a
mandatory `ProvenanceEntry`. The `report.json` sidecar exposes the full audit trail,
so a reviewer can reproduce every number.

**The grounding contract.** Synthesis has to cite a real record or analysis ID for
every quantitative claim. The Critic mechanically checks that each cited ID exists
on the Blackboard and that no quantitative claim is left uncited, and a dangling or
missing citation is an automatic "revise". The LLM handles planning, synthesis, and
critique, but it never does the arithmetic.

**Deterministic core.** All statistics are pure, unit-tested functions
(`fra/analysis/`), verified on synthetic series with known trends and lags. The
same functions power both the analysis agent and the figures.

**Gaps are data.** Missing coverage is recorded and surfaced under *Limitations*,
never fabricated.

**Reproducible runs.** On-disk connector caching, provenance, and `run_log.jsonl`
together mean any result can be re-derived and audited.

## Repository layout

```
src/fra/
├── orchestrator.py      state machine + phase log
├── blackboard.py        shared per-run state (§4)
├── llm.py               Anthropic wrapper + structured-output contract (§7)
├── offline.py           deterministic keyless backend
├── config.py            connectors.yaml loader
├── taxonomy.py          WoRMS resolver + cache
├── spatial.py           FAO area / GFCM GSA normalization
├── agents/              planner, retrieval, oceanography, analysis, literature, synthesis, critic
├── connectors/          one file per source + registry
├── analysis/            trends.py, status.py, association.py (pure, tested)
├── models/              all pydantic contracts (§5)
├── report/              render.py (md + json), figures.py (matplotlib)
├── prompts/             versioned prompt templates
└── cli.py               `fra run "..."`
tests/                   models, analysis, connectors (offline), agents, orchestrator (e2e)
examples/hake_area_37.md committed sample report
```

## Development

```bash
ruff check src tests      # lint
ruff format --check src tests
mypy                      # strict type-check
pytest -q                 # offline, deterministic; no keys/network needed
```

Tests are offline-first. Connector tests run against recorded response shapes via
httpx `MockTransport`, analysis functions are checked on synthetic data with known
answers, agents run under a mocked or offline LLM, and one end-to-end test drives a
full fixture question through the whole graph and asserts a well-formed `FinalReport`
with non-empty citations and a populated provenance log. CI
(`.github/workflows/ci.yml`) runs lint, format, type-check, and tests on Python
3.10 through 3.12, plus a keyless offline smoke run of the example question.

## License

MIT.
