# Nature Breakdown API

## Overview

Nature Breakdown API extracts structured, ontology-ready clinical intents from unstructured medical text using a Vertex AI (Gemini) LLM pipeline.

**Service version:** `1.1.0`

**Key Features:**
- Accepts single or multiple clinical text inputs
- Expands medical abbreviations and implicit context
- Detects whether input is clinical (v1)
- Returns validated, structured clinical intents
- **v2:** representative terms and optional retrieval signals for downstream document retrieval

## Architecture

```
Client
  │
FastAPI
  ├─ /v1/extract-intents     (Simplified)
  ├─ /v1/nature-breakdown    (Full v1 pipeline)
  ├─ /v2/nature-breakdown    (v2 pipeline + optional retrieval signals)
  └─ /v1/retrieval-signals (Standalone retrieval signals)
  │
ContextualIntentPipeline
  ├─ Query Expansion (LLM)
  ├─ Intent Extraction (LLM)
  ├─ Representative Terms (LLM, v2)
  ├─ Contextual Environment (LLM, v2 when enable_retrieval_signals=true)
  ├─ Pydantic Validation / signal assembly
  │
Structured JSON Response
```

**Version routing:** Pipeline version is selected by URL path only. `/v1/*` stays on the legacy pipeline. `/v2/nature-breakdown` always uses v2 prompts; `enable_retrieval_signals` only gates the contextual-environment / retrieval-signals step.

| Endpoint | Pipeline | Notes |
|---|---|---|
| `/v1/extract-intents` | v1 | Simplified intents |
| `/v1/nature-breakdown` | v1 | Full nature breakdown; ignores signals flag |
| `/v2/nature-breakdown` | v2 | Representative terms; optional `final_candidates` + `retrieval_signals` |
| `/v1/retrieval-signals` | v2 | Standalone retrieval signals; output ids are generated |

---

## Getting Started

Follow these steps to get the project running on your local system.

### Installation

1. Ensure Python 3.12+ is installed on your system.

2. Clone the repository:
   ```bash
   git clone <repo-url>
   cd <repo-folder>
   ```

3. Create a virtual environment and activate it:
   ```bash
   python -m venv .venv
   source .venv/bin/activate   # On Windows: .venv\Scripts\activate
   ```

4. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Running the Application

Start the service locally:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Running Tests

Execute the unit test cases using pytest:

```bash
pytest
```

For more detailed test output:

```bash
pytest -v -s
```

### API References

After running the service locally, access the interactive API documentation at:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### Temporal vocabulary (v2 retrieval signals)

CUI-resolved temporal windows need a name→CUI vocabulary loaded **once at process startup** (not per request).

| Variable | Required | Description |
|---|---|---|
| `TEMPORAL_GCS_BUCKET` | No | GCS bucket. When set (with path), vocab is loaded from GCS. |
| `TEMPORAL_GCS_PATH` | No | Object path in the bucket (default blob path used when bucket is set). |
| `TEMPORAL_LOCAL_PATH` | No | Local JSON file used when bucket is not set, **or when GCS load fails**. Default: `app/temporal_name_to_cui.json`. |

**Behavior:**
- Prefer GCS when `TEMPORAL_GCS_BUCKET` is set; on GCS failure (or when bucket is unset), load from `TEMPORAL_LOCAL_PATH`.
- If both fail, v2 still runs but temporal falls back to `Recent` / `C0332185`.
- Updating the JSON in GCS or on disk requires a process restart (or new Cloud Run instance) to take effect.
- For Cloud Run: set the GCS env vars, **or** ship the JSON in the image (local backup runs automatically if GCS fails).

### Record-type vocabulary (`/v1/retrieval-signals`)

Record-type CUIs need a name→CUI vocabulary, loaded **once at process startup** using the same GCS-first / local-fallback convention as the temporal vocabulary.

| Variable | Required | Description |
|---|---|---|
| `RECORD_TYPE_GCS_BUCKET` | No | GCS bucket for the record-type vocabulary. Default `""`. |
| `RECORD_TYPE_GCS_PATH` | No | Blob path. Default: `Nature_breakdown/record_type_name_to_cui.json`. |
| `RECORD_TYPE_LOCAL_PATH` | No | Local JSON file used when bucket is not set, **or when GCS load fails**. Default: `app/record_type_name_to_cui.json`. |

**Behavior:**
- Vocabulary file shape (identical to the temporal vocab): `{ "Radiology Report": [{"cui": "C0034571"}], ... }`. Lookup is case- and underscore-insensitive (`radiology_report` matches `Radiology Report`). Keys starting with `_` are ignored.
- The loaded labels are injected into the contextual-environment prompt so the model matches against real vocabulary entries instead of inventing free-text labels.
- If both sources fail, the endpoint still responds but record types carry an empty `coding` list.

### Configuration — where things live

- `app/config.py` holds **every** setting with its default and the explanation: the model
  and GCP settings, the vocabularies, and the whole record-type coding block (project,
  dataset, tables, service URLs, cluster set, batching, time limits). The record-type block
  carries the same values as the standalone link-code pipeline script, so the service runs
  against the cluster / transaction services **without anything being filled in**.
- `config/.env.<env>` is a plain `KEY=VALUE` list per environment (dev / test / stage /
  prod / aif), turned into Cloud Run environment variables by
  `azure-pipelines-for-app-config.yml`. A key there overrides the default in
  `app/config.py` for that environment; a **blank** value (`KEY=`) keeps the default.
  Anything not listed keeps its default. The files list the values that are worth seeing
  per environment:

| Key | Default (`app/config.py`) |
|---|---|
| `PROJECT_ID` | — (the project the service runs in: Vertex AI, GCS vocabularies) |
| `GCP_LOCATION`, `MODEL_VERSION` | `us-central1`, `gemini-3.5-flash` |
| `CLUSTER_SELECTION_URL` | `https://cluster-selection-api-68004442910.us-central1.run.app/cluster-selection/v2/cui_matching` |
| `TRANSACTION_SELECTION_URL` | `https://transaction-selection-api-705290722717.us-central1.run.app/transaction_selection` |
| `CLUSTER_BQ_PROJECT` / `CLUSTER_BQ_DATASET` / `CLUSTER_BQ_LOCATION` | `aif-usr-p-ep-cg-wcdm-0767` / `aif_wcdm_osm_pipeline` / `US` |
| `CLUSTER_ACTIVITY_TABLE`, `CLUSTER_ALL_RESULTS_TABLE` | `SCENARIO_6_ACTIVITY_CODE_DETAILS_ARRAY`, `all_results` |
| `CLUSTER_SET`, `CLUSTER_TOP_K`, `CLUSTER_TOP_P`, `CLUSTER_COMBINED` | `loinc_document_v001`, `10`, `20`, `true` |
| `TRANSACTION_SELECTION_MODEL`, `TRANSACTION_BATCH_SIZE`, `TRANSACTION_MAX_WORKERS` | `gemini-2.5-flash`, `25`, `30` (as the pipeline script) |
| `RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS`, `TRANSACTION_REQUEST_TIMEOUT_SECONDS`, `CLUSTER_REQUEST_TIMEOUT_SECONDS` | `210`, `300`, `120` — the budget for coding a query's record types, and the script's per-call timeouts (see the pipeline description below); keep the budget plus the model steps inside the request timeout in front of the service (Cloud Run default 300 s) |
| `ALLOW_LOCAL_GCLOUD_AUTH`, `GCLOUD_PATH` | `true`, — : local runs get identity tokens from `gcloud auth print-identity-token` as the script does (Cloud Run uses the metadata server); set `GCLOUD_PATH` when gcloud is not on `PATH` |
| `TEMPORAL_GCS_BUCKET` / `TEMPORAL_GCS_PATH`, `RECORD_TYPE_GCS_BUCKET` / `RECORD_TYPE_GCS_PATH` | vocabularies from GCS, falling back to the bundled files |

Permissions for the service account: invoker on the cluster-selection and transaction-selection services, BigQuery Data Viewer + Job User on the dataset, Vertex AI user, and read on the GCS vocabularies. Startup logs `Record-type pipeline configured for /v2 and /v3 retrieval signals` with the URLs, project, dataset, tables, batch size and workers it will use.

### Record types + Action Finder (`/v2` and `/v3/retrieval-signals`)

Record-type **names** come from the nature breakdown, as production `/v1` generates them; the record-type vocabulary is never sent to the model (no `KNOWN RECORD TYPES` block) and the model never matches record types. Their **codes** come from the standalone link-code pipeline and nothing else. For each record type, with the query's tag names joined by `", "` as the context (the name itself when there are no tags):

1. **Cluster selection** — `POST CLUSTER_SELECTION_URL` `{cluster_set, text_list: [name], context_list: [context], top_k, top_p, combined}`; every `member_name` in the answer is a probable document.
2. **Activities** — `CLUSTER_ACTIVITY_TABLE`: the activities of those documents.
3. **Stage 1** — `POST TRANSACTION_SELECTION_URL` in batches of `TRANSACTION_BATCH_SIZE` (`TRANSACTION_MAX_WORKERS` concurrent): is each activity related? (Yes/No)
4. **Related documents** — `CLUSTER_ACTIVITY_TABLE`: the documents of the selected activities.
5. **Stage 2** — is each document related?
6. **Document classes** — `CLUSTER_ALL_RESULTS_TABLE` (`document_type`, `document_class`, `node_id`) of the selected documents.
7. **Stage 3** — is each class related? The selected classes' `node_id`s, without the row suffix (`C3166217-0` → `C3166217`), are the record type's `coding`.

The payloads and the BigQuery queries are the script's own (names matched exactly as the previous step returned them). As in the script, a step that finds nothing ends the run for that record type: its `coding` is empty, and the log says which step stopped it. There is no other source of codes.

- Record types of one query run concurrently (`CLUSTER_SELECTION_MAX_WORKERS`); each logs one `Record-type transaction pipeline` line with the counts per step, the `outcome` (`ok`, `no probable documents`, `no activity selected`, `no document selected`, `no class selected`, `deadline reached …`) and the seconds taken. Record types left without codes are listed once per query (`Record types the pipeline could not code`).
- Transaction-selection calls are retried as in the script (answer-count mismatch, 429 / 5xx, network errors); a batch that still fails contributes nothing. A failing cluster-selection call or BigQuery query is logged at `ERROR` and leaves that record type uncoded — it never turns the request into an error.
- Time: the coding of all record types of a query runs against `RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS` (210 s): every call is capped by the time left, no retry sleeps past it, and when it runs out the pipeline stops where it is (classes already confirmed in Stage 3 are kept; the log line's `outcome` says `deadline reached …`). Per-call timeouts as the script: `TRANSACTION_REQUEST_TIMEOUT_SECONDS` (300), `CLUSTER_REQUEST_TIMEOUT_SECONDS` (120). If the logs show `deadline reached`, raise the budget together with the request timeout in front of the service.
- Authentication: on Cloud Run the identity tokens come from the attached service account (metadata server), one per service URL, cached and refreshed before expiry (and once more on `401/403`). Locally, `gcloud auth print-identity-token` as the script (`ALLOW_LOCAL_GCLOUD_AUTH`, `GCLOUD_PATH`).
- `ACTION_FINDER_MODEL` (optional) is the model for the Action Finder classification; default the request's `model_name`, else `MODEL_VERSION`. The Action Finder is an in-process LLM classification (never an HTTP hop); its token usage is reported under `usage_metadata.action_finder`.
- `503 cluster_service_unavailable` only when `CLUSTER_SELECTION_URL`, `TRANSACTION_SELECTION_URL` or `CLUSTER_BQ_DATASET` is configured blank, answered before any model call.

### Canonical temporal index + cadence memory (`/v3/retrieval-signals`)

`/v3/retrieval-signals` never sends the temporal vocabulary to the model. At startup the loaded vocabulary (same `TEMPORAL_*` sources as above) is indexed in-process: every entry's formula is parsed into a canonical window (`last 6 months`, `between 6 and 12 months`, …), unit codes are learned from the file, and entry names get a similarity index. At request time the model writes each window as a structured span and code resolves it against the index. Nothing about the vocabulary, its defaults or any clinical cadence is written in code; if the vocabulary could not be indexed, `/v3` answers `503 temporal_index_unavailable` while `/v1` and `/v2` keep working.

| Variable | Required | Description |
|---|---|---|
| `TEMPORAL_INDEX_SIMILARITY_PROVIDER` | No | `lexical` (in-process character n-gram TF-IDF, no network) or `vertex` (Vertex text embeddings, cached on disk; falls back to lexical on failure). Default `lexical`. |
| `TEMPORAL_EMBEDDING_MODEL` | No | Embedding model for the `vertex` provider. |
| `TEMPORAL_INDEX_CACHE_DIR` | No | Directory for the embedding cache (keyed by model + vocabulary hash). |
| `TEMPORAL_MAX_CODES_PER_WINDOW` | No | Distinct CUIs kept per resolved window, ranked by closeness to the model's wording. Default `2`. |
| `TEMPORAL_MIN_SIMILARITY` | No | Closeness (entry name vs the model's wording) below which similarity is ignored for ranking and additional entries for the same window are dropped. Default `0.3`. |
| `TEMPORAL_MENU_MAX_VALUES` / `TEMPORAL_MENU_UNITS` | No | Codeable-window menu shown to the model: values per unit (most represented first) and which units. Defaults `12` / `day,week,month,year`. |
| `TEMPORAL_SHORTLIST_TOP_N` / `TEMPORAL_SHORTLIST_MIN_SCORE` | No | Query-time shortlist of vocabulary names close to the query wording (only when the query states a span). Defaults `8` / `0.3`. |
| `TEMPORAL_SHADOW_MODE` | No | `true` makes `/v3` also run the legacy vocabulary-list path and log per-candidate agreement (`Temporal shadow comparison`). Doubles the contextual-environment call; rollout only. Default `false`. |
| `CADENCE_MEMORY_GCS_BUCKET` / `CADENCE_MEMORY_GCS_PATH` | No | Cadence-memory file in GCS (default path `Nature_breakdown/cadence_memory.json`). |
| `CADENCE_MEMORY_LOCAL_PATH` | No | Local fallback. Default `app/cadence_memory.json` (absent → memory disabled, `/v3` still runs). |
| `CADENCE_MEMORY_EXAMPLES_PER_CONCEPT` / `CADENCE_MEMORY_MAX_CONCEPTS` / `CADENCE_MEMORY_MIN_SIMILARITY` | No | How many previously inferred windows are shown per concept, for how many of the query's concepts, and how close a concept name must be to a remembered one. Defaults `3` / `10` / `0.6`. |

**Behavior:**
- `/v3` uses its own prompt, `app/prompts/v3/contextual_environment.py` (a frozen copy of the v2 template with the vocabulary-list slot replaced by the `CANONICAL WINDOW` section); `/v1` and `/v2` keep `app/prompts/v2/contextual_environment.py` unchanged. Its temporal section contains only data derived at request time: the codeable-window menu (from the index), the shortlist (from the query) and, when a cadence-memory file is loaded, the windows the service itself inferred before for the query's concepts (`WINDOWS THIS SERVICE HAS INFERRED BEFORE …`). Measured on the bundled temporal vocabulary the contextual-environment prompt is ≈17.5K characters instead of `/v1`'s ≈82.8K (≈4.4K vs ≈20.7K tokens). Neither `/v2` nor `/v3` ever adds the record-type vocabulary, so their prompt size does not grow with it (in `/v1` a GCS record-type vocabulary is pasted into the prompt as the `KNOWN RECORD TYPES` block).
- Startup logs `Temporal index built for /v3/retrieval-signals` with coverage: `keyed_by_formula`, `keyed_by_name` (entries whose formula did not parse but whose name states a span), `unkeyed` and `unkeyed_samples` — check this first when windows resolve to the default. The model is asked for a strict schema (`window`, `basis`, `rationale` required on every entry); a malformed window drops only that entry's window.
- Every resolved window is logged as one `Temporal inference` event (query, candidate, basis, rationale, window, formula, CUI, index and memory versions). These events are the only input of the cadence memory and of the evaluation scripts.
- Cadence memory is built, never authored: `python scripts/build_cadence_memory.py --input <exported log JSONL> --output app/cadence_memory.json [--previous <current file>] [--upload gs://bucket/path]`. The job prints a drift report (top window changed / distribution shift per concept) and exits `3` when any concept moved more than `--max-shift`, so a scheduled run can refuse to publish a memory that moved.
- Evaluation before switching traffic: `python scripts/replay_retrieval_signals.py --base-url http://host/intent-nature-breakdown --queries queries.txt --versions v2 v3 --output replay.jsonl`, then `python scripts/evaluate_temporal.py --replay replay.jsonl --baseline v2 --candidate v3 [--min-agreement 0.995] [--judge-model gemini-2.5-flash --project <id>]`. The report gives CUI/formula agreement, unresolved rates, mean prompt tokens and latency per version and, with a judge model, an accept rate over inferred windows with only the rejections listed for a human to sample. Exit code `2` below the agreement threshold for CI.

---

## Troubleshooting

### Installing `aie_logging_utility==0.1.0` fails

If you encounter errors while installing the `aie_logging_utility` package, follow these steps:

1. **Update pip** - Ensure you are using the latest version:
   ```bash
   python -m pip install --upgrade pip
   ```

2. **Install required keyrings** - Some private packages require authentication via keyring:
   ```bash
   pip install keyring artifacts-keyring
   ```

3. **Configure pip to use the private package index** - Create a `pip.ini` file (Windows) or `pip.conf` file (Mac/Linux) in your virtual environment with the following content:
   ```ini
   [global]
   index-url = https://pkgs.dev.azure.com/mclm/_packaging/KMD/pypi/simple/
   ```

4. **Install the package again**:
   ```bash
   pip install aie_logging_utility==0.1.0
   ```

---

## Endpoints

The API provides six endpoints:

1. **`/v1/extract-intents`** — Simplified response (intent title and description only)
2. **`/v1/nature-breakdown`** — Full v1 response (nature, sub_natures, `final_queries`)
3. **`/v2/nature-breakdown`** — v2 response (representative terms; optional retrieval signals)
4. **`/v1/retrieval-signals`** — standalone retrieval signals; accepts comma-separated text and generates output ids
5. **`/v2/retrieval-signals`** — same request as `/v1/retrieval-signals` plus optional Action Finder gating (`actions`); record-type CUIs come from the link-code pipeline instead of the JSON vocabulary, and `temporal` is a list with one entry per distinct window. `GET /v2/retrieval-signals/action-types` lists the selectable actions.
6. **`/v3/retrieval-signals`** — same request and response as `/v2`; only the temporal **matching** changes: the temporal vocabulary is indexed in-process instead of being pasted into the prompt, the model states each window as a structured span and code resolves it to the vocabulary entry (exact, else the narrowest containing window). Queries with no time expression get a window inferred per candidate from its clinical nature, resolved the same way.

v1 and v2 nature-breakdown accept the same base request shape. `enable_retrieval_signals` is only used by `/v2/nature-breakdown`.

### Request Schema

```json
{
  "texts": [
    "Patient with diabetes mellitus and shortness of breath on exertion",
    "current medication"
  ],
  "model_name": "gemini-2.5-flash",
  "generation_config": {
    "temperature": 0.2,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 64536,
    "thinking_config": {
      "thinking_budget": 1000
    }
  },
  "enable_retrieval_signals": false,
  "location": "us-central1"
}
```

**Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `texts` | string \| string[] | Yes | Single text, or list of texts to process |
| `model_name` | string | No | LLM model override (default: `MODEL_VERSION` env, fallback `gemini-3.5-flash`). Must be one of the [allowed models](#allowed-generation-models). |
| `generation_config` | object | No | LLM generation settings (see below) |
| `enable_retrieval_signals` | boolean | No | **v2 only.** When `true`, runs contextual environment and attaches `final_candidates` / `retrieval_signals`. Default `false`. Ignored by `/v1/*`. |
| `location` | string | No | GCP region for routing the LLM call. Allowed values: `us`, `us-central1`. Any other value is rejected with `422`. When omitted, see [Location Routing](#location-routing) for how it's resolved. Applies to all four endpoints. |

**Generation Config Options:**

| Option | Type | Range | Description |
|--------|------|-------|-------------|
| `temperature` | float | 0.0-2.0 | Sampling temperature (higher = more creative) |
| `top_p` | float | 0.0-1.0 | Nucleus sampling parameter |
| `top_k` | int | ≥0 | Top-k sampling parameter |
| `max_output_tokens` | int | ≥1 | Maximum output tokens |
| `thinking_config` | object | - | Thinking mode configuration |

**Thinking Config:**

| Option | Type | Description |
|--------|------|-------------|
| `thinking_budget` | int | Token budget for thinking/reasoning (0 to disable) |

### Allowed Generation Models

`model_name` must be one of the values in `ALLOWED_GEN_MODELS` (`app/config.py`). Unknown models are rejected with **422** before any Vertex call.

| Model |
|-------|
| `gemini-2.5-flash` |
| `gemini-2.5-flash-lite` |
| `gemini-3.1-flash-lite` |
| `gemini-3.5-flash` |
| `gemini-3.6-flash` |
| `gemini-3.7-flash` |
| `gemini-3.8-flash` |
| `gemini-3.5-flash-lite` |

When `model_name` is omitted, the service uses the `MODEL_VERSION` environment variable (must also be on the allowlist).

### Location Routing

`location` pins the LLM call to a specific GCP region instead of the service default. Allowed values: `us`, `us-central1` — any other value is rejected at request validation with `422`.

Resolution order when `location` is not provided (highest priority first):

1. **Model-family default** — `MODEL_LOCATION_DEFAULTS` in `app/utils/location.py`, matched by longest model-name prefix. Empty today, so this tier is currently inert; fill it in once a model family needs a non-default region.
2. **Configured `GCP_LOCATION`** environment variable.
3. **Fallback** — `us-central1` (`DEFAULT_LOCATION`).

Applies uniformly to `/v1/extract-intents`, `/v1/nature-breakdown`, and `/v2/nature-breakdown`. The pipeline caches one Vertex AI client per distinct location it sees, so switching locations across requests doesn't re-authenticate each time.

### Error Responses

LLM-call failures surface as an HTTP error with a structured body:

```json
{
  "detail": {
    "error": "llm_location_error",
    "message": "Requested model/location is not available: 404 Publisher Model ... was not found",
    "details": {
      "original_error": "...",
      "model": "gemini-2.5-flash",
      "location": "us"
    }
  }
}
```

| Status | `error` | Cause |
|---|---|---|
| 422 | `validation_error` | Empty `texts`, whitespace-only text, an invalid `location`, an invalid `model_name`, or (`/v2/retrieval-signals`) an unknown action in `actions` |
| 400 | `invalid_llm_request` | Malformed request to the LLM (e.g. bad `generation_config`) that isn't a location/model issue |
| 404 | `llm_location_error` | The requested model/location combination is unavailable downstream (model not offered in that region, etc.) |
| 429 | `rate_limit_exceeded` | LLM provider rate limit hit — retry later |
| 502 | `llm_error` | Unclassified LLM call failure |
| 503 | `service_unavailable` | LLM service temporarily unavailable (e.g. regional outage) — the service does **not** fail over to another region |
| 503 | `cluster_service_unavailable` | `/v2` and `/v3/retrieval-signals` only: the document-cluster dependency is **not configured** (`details.missing_settings`); answered before any model call. A cluster-selection / transaction-selection / BigQuery failure **during** a request is not an error: it is logged (`ERROR`) and the record types keep the coding the remaining stages can give them (see the record-type stages) |
| 504 | `llm_timeout` | LLM call exceeded the 300s timeout |

For a batch (`texts` with multiple entries), the first failure aborts the whole request — errors are not accumulated per-item.

---

### [POST] `/v1/extract-intents`

Extract clinical intents with simplified response. Returns only intent title and description for each intent.

**Use Case:** When you only need basic intent information without detailed breakdown.

#### Response Structure

```json
{
  "status": 1,
  "output": {
    "total_inputs": 1,
    "results": [
      {
        "input_index": 0,
        "original_query": "Patient with DM and SOB",
        "intents": [
          {
            "intent": "Diabetes Mellitus",
            "intent_description": "Patient has diabetes mellitus condition"
          },
          {
            "intent": "Shortness of Breath",
            "intent_description": "Patient experiencing shortness of breath"
          }
        ]
      }
    ]
  },
  "details": {
    "timing": {
      "total_llm_seconds": 15.867464,
      "max_llm_seconds": 15.867464,
      "max_llm_seconds_input_index": 0
    },
    "usage_metadata": {
      "query_expansion": {
        "prompt_token_count": 150,
        "candidates_token_count": 50,
        "total_token_count": 200,
        "thinking_token_count": 0
      },
      "intent_extraction": {
        "prompt_token_count": 500,
        "candidates_token_count": 300,
        "total_token_count": 800,
        "thinking_token_count": 0
      }
    },
    "version": "1.0.0",
    "timestamp": "2026-01-14T19:40:20.712694",
    "source": null
  },
  "service": "intent-nature-breakdown-api-1.0.0"
}
```

**Response Fields:**
- `status` (int): `1` for success, `0` for errors (e.g., validation failures)
- `output` (object): Contains results and count:
  - `total_inputs` (int): Number of input texts processed
  - `results` (array): Array of results, each containing:
    - `input_index` (int): Index of the input text
    - `original_query` (string): Original query text
    - `intents` (array): Array of extracted intents
    - For clinical queries: Also contains `is_clinical: true`, `expanded_query`, `abbreviations_expanded`, etc.
    - For non-clinical queries: Contains `intents: []`, `is_clinical: false`, `rejected_reason`, `expanded_query`
    - For errors: Contains `error` field with error details
- `details` (object): Metadata and timing information:
  - `timing` (object): Processing time metrics
    - `total_llm_seconds` (float): Sum of processing times for all inputs
    - `max_llm_seconds` (float): Maximum processing time across all inputs
    - `max_llm_seconds_input_index` (int): Index of the input that took the longest
    - `usage_metadata` (object): Token usage for audit/cost tracking
    - `query_expansion` (object): Tokens used in query expansion step
    - `intent_extraction` (object): Tokens used in intent extraction step
    - Each contains: `prompt_token_count`, `candidates_token_count`, `total_token_count`, `thinking_token_count`
  - `version` (string): API version
  - `timestamp` (string): Response timestamp (ISO 8601)
  - `source` (string|null): Source identifier if provided in request
- `service` (string): Service identifier

**Note:** `timestamp` and `processing_time_seconds` are no longer included in individual result objects. They are aggregated in `details.timing` and `details.timestamp`.

---

### [POST] `/v1/nature-breakdown`

Extract clinical intents with full nature breakdown. Returns complete structured intents including nature, sub_natures, and final_queries.

**Use Case:** When you need detailed intent analysis with hierarchical categorization and atomic queries. This endpoint is unchanged in `1.1.0` (legacy v1 pipeline).

#### Response Structure

The API returns structured clinical intents with the following format:

```
Intent
 ├── intent_title
 ├── description
 ├── nature                    ← primary informational role
 ├── sub_natures[]             ← constituent conceptual components and sub set of Nature
 │    ├── category_path        ← flattened, ordered hierarchy (canonical vocabulary) which is a subset of nature/sub nature
 │    ├── atomic_concepts[]    ← smallest ontology-mappable units in all levels
 └── final_queries[]           ← derived from all the above, especially from atomic concepts
```

---

### [POST] `/v2/nature-breakdown`

v2 nature breakdown with representative terms and optional retrieval signals.

**Always runs the v2 pipeline** (query expansion → intent extraction → representative terms). When `enable_retrieval_signals` is `true`, also runs contextual environment and assembles retrieval metadata for document search.

**Use Case:** Downstream retrieval that needs record types, authors, longitudinal scope, content signals, clinical setting, and CUI-resolved temporal windows.

#### Response fields (additive vs v1)

| Field | When present | Description |
|---|---|---|
| `representative_terms` | Always (v2) | Canonical clinical entities distilled from the expanded query |
| `final_queries` | Always | Kept for compatibility (string list per intent) |
| `final_candidates` | Signals on | One candidate object per atomic concept, with `candidate_id`, `sub_nature`, `candidate`, and `retrieval_signals` |
| `retrieval_signals` | Signals on | On each candidate **and** each intent. Intent `temporal` is the deduped union (by CUI) of its candidates' resolved temporal entries |

Each `retrieval_signals` block includes:

- `record_types`, `authors`, `longitudinal_scope`, `content_signals`, `clinical_setting`
- `temporal`: `[{ "time_window", "codes", "formula" }, ...]`

#### Example (signals on)

```python
import requests

payload = {
    "texts": "elevated psa in last 3 years",
    "enable_retrieval_signals": True,
}

response = requests.post(
    "http://localhost:8000/v2/nature-breakdown",
    headers={"Content-Type": "application/json"},
    json=payload,
)
print(response.json())
```

**Truncated response shape:**

```json
{
  "status": 1,
  "output": {
    "total_inputs": 1,
    "results": [
      {
        "input_index": 0,
        "original_query": "elevated psa in last 3 years",
        "expanded_query": "...",
        "representative_terms": ["PSA", "prostate disease"],
        "total_intents_detected": 2,
        "intents": [
          {
            "intent_title": "Elevated PSA Laboratory Values",
            "description": "...",
            "nature": "Laboratory / Result",
            "sub_natures": [],
            "final_queries": ["elevated PSA", "PSA laboratory values"],
            "final_candidates": [
              {
                "candidate_id": "fc_001",
                "intent_title": "Elevated PSA Laboratory Values",
                "nature": "Laboratory / Result",
                "sub_nature": "Laboratory Test / Biomarker",
                "candidate": "prostate-specific antigen",
                "retrieval_signals": {
                  "record_types": ["laboratory_report", "progress_note"],
                  "temporal": [
                    {
                      "time_window": "In past 3 years",
                      "codes": "C3843792",
                      "formula": null
                    }
                  ],
                  "authors": ["pathologist", "urologist"],
                  "longitudinal_scope": ["screening", "diagnostic_workup"],
                  "content_signals": ["loinc_lab_code", "measurement_value"],
                  "clinical_setting": ["outpatient"]
                }
              }
            ],
            "retrieval_signals": {
              "record_types": ["laboratory_report", "progress_note"],
              "temporal": [
                {
                  "time_window": "In past 3 years",
                  "codes": "C3843792",
                  "formula": null
                }
              ],
              "authors": ["pathologist", "urologist"],
              "longitudinal_scope": ["screening", "diagnostic_workup"],
              "content_signals": ["loinc_lab_code", "measurement_value"],
              "clinical_setting": ["outpatient"]
            }
          }
        ]
      }
    ]
  },
  "details": {
    "usage_metadata": {
      "query_expansion": {},
      "intent_extraction": {},
      "representative_terms": {},
      "contextual_environment": {}
    },
    "version": "1.1.0"
  },
  "service": "intent-nature-breakdown-api-1.1.0"
}
```

With `enable_retrieval_signals: false`, v2 still returns `representative_terms` and intents with `final_queries`, but omits `final_candidates` / `retrieval_signals` (and does not call contextual environment). See Swagger for the full OpenAPI schema.

---

### [POST] `/v1/retrieval-signals`

Returns normalized record types, temporal validity windows, and representative tags for each input query. Query IDs are generated in the response; callers do not provide them.

Provide `text` as a JSON array. Each array item is processed as one complete query and receives its own generated output ID (`q1`, `q2`, and so on):

#### Request

```json
{
  "queries": [
    {
      "text": [
        "chest x-ray reports from the last year",
        "tell me a joke"
      ]
    }
  ]
}
```

The endpoint also accepts optional `record_types`, `temporal`, and `tags` hints on each query. Record-type and tag hints are merged ahead of derived values. A temporal hint overrides the derived temporal result.

`model_name`, `generation_config`, and `location` are also accepted at the top level and behave exactly as on the other endpoints — see [Location Routing](#location-routing).

#### Response

```json
{
  "queries": [
    {
      "id": "q1",
      "text": "chest x-ray reports from the last year",
      "record_types": [
        {
          "name": "Clinical Note",
          "coding": [
            {
              "code": "C0332185"
            },
            {
              "code": "C0236123"
            }
          ]
        },
        {
          "name": "Radiology Report",
          "coding": [
            {
              "code": "C0034571"
            }
          ]
        }
      ],
      "temporal": {
        "name": "Last One Year",
        "formula": ["REF_POINT", "REF_POINT - 1Y"],
        "coding": [
          {
            "system": "UMLS",
            "code": "C0332185"
          }
        ]
      },
      "tags": [
        {
          "name": "Chest X-ray",
          "coding": [],
          "topics": [
            "imaging",
            "radiology",
            "chest"
          ]
        }
      ]
    }
  ]
}
```

`queries` must contain at least one item, and each item must contain non-empty `text`. Empty or whitespace-only text returns `422`.

---

### [POST] `/v2/retrieval-signals`

Same request body, response envelope and per-query keys as `/v1/retrieval-signals`, with these differences:

1. **Action Finder gate** — optional top-level `actions` (plus `enable_action_finder`, default `true`). When `actions` is supplied, each query is first classified in-process by the Action Finder. Retrieval signals are computed only for queries whose identified actions overlap `actions` **and** include `information_retrieval`; every other query returns a *skip block* instead of signals (HTTP 200 — "not serviceable by retrieval" is a result, not a client error). Omitting `actions` (or sending `[]`, or `enable_action_finder: false`) keeps the ungated behaviour. An action name outside the taxonomy returns `422` with `invalid_actions`. If the Action Finder itself fails, the gate fails **open** (the query is processed) and the failure is logged.
2. **Record-type codes from the link-code pipeline** — `record_types[].coding` are the node ids the standalone pipeline selects for each record type (see [Record types + Action Finder](#record-types--action-finder-v2-and-v3retrieval-signals)); the `record_type_name_to_cui.json` vocabulary is not consulted. A record type the pipeline finds nothing for has an empty `coding`. `503 cluster_service_unavailable` only when the pipeline is configured blank (before any model call); a failure during a request is logged and leaves that record type uncoded, never the response.
3. **`temporal` is a list** — one `{name, formula, coding}` per distinct window the nature breakdown resolved for the query (a query about two things with different look-back periods gets both), in pipeline order, so the first entry is the window `/v1` returns. Windows with the same formula (two vocabulary names for the same span, e.g. `3 years` and `In past 3 years`) are one entry with the CUIs of both. The default window (`Recent`) is listed only when nothing else was resolved. `formula` always has two terms, `[start, end]`, as in `temporal_name_to_cui.json`: a one-term formula (a vocabulary file that stores the window as one string, as the AIF GCS file does, or a client hint `REF_POINT - 1Y`) is returned as `["REF_POINT", "REF_POINT - 1Y"]`. A `temporal` hint replaces the list with that one window.

Selectable actions (`GET /v2/retrieval-signals/action-types`): `admin_response`, `conversational_response`, `information_retrieval`, `knowledge_fact_search`, `out_of_scope`. Matching is case-, space- and hyphen-insensitive (`"Information Retrieval"` == `information_retrieval`).

#### Request

```json
{
  "actions": ["information_retrieval"],
  "queries": [
    {
      "text": [
        "chest x-ray reports from the last year",
        "book me a cardiology appointment Tuesday"
      ]
    }
  ]
}
```

`model_name`, `generation_config`, `location` and the per-query `record_types` / `temporal` / `tags` hints behave exactly as on `/v1/retrieval-signals`.

#### Response

```json
{
  "status": 1,
  "output": {
    "total_queries": 2,
    "queries": [
      {
        "id": "q1",
        "text": "chest x-ray reports from the last year",
        "record_types": [
          {
            "name": "Radiology Report",
            "coding": [
              { "code": "C0034571" },
              { "code": "C0011923" }
            ]
          }
        ],
        "temporal": [
          {
            "name": "Last One Year",
            "formula": ["REF_POINT", "REF_POINT - 1Y"],
            "coding": [
              { "system": "UMLS", "code": "C0332185" }
            ]
          }
        ],
        "tags": [
          {
            "name": "Chest X-ray",
            "coding": [],
            "topics": ["imaging", "radiology", "chest"]
          }
        ]
      },
      {
        "id": "q2",
        "text": "book me a cardiology appointment Tuesday",
        "skipped": true,
        "skip_reason": ["admin_response"],
        "skip_reason_detail": "Action Finder classified this query as admin_response",
        "record_types": [],
        "temporal": [],
        "tags": []
      }
    ]
  },
  "details": {
    "timing": { "total_llm_seconds": 6.2, "max_llm_seconds": 6.2, "max_llm_seconds_input_index": 0 },
    "usage_metadata": {
      "action_finder": { "prompt_token_count": 1800, "candidates_token_count": 20, "total_token_count": 1820, "thinking_token_count": 0 },
      "query_expansion": { "...": "..." },
      "intent_extraction": { "...": "..." },
      "representative_terms": { "...": "..." },
      "contextual_environment": { "...": "..." }
    },
    "version": "1.5.16",
    "timestamp": "2026-09-23T14:02:11.120334+00:00",
    "source": null,
    "model": "gemini-3.5-flash",
    "location": "us"
  },
  "service": "intent-nature-breakdown-api-1.5.16"
}
```

Skip block contract: `skipped: true`, `skip_reason` is the machine-readable list of actions the Action Finder identified, `skip_reason_detail` is the human-readable explanation, and the signal fields keep the same shape as a computed block (`record_types: []`, `temporal: []`, `tags: []`) so every entry parses the same way. `usage_metadata.action_finder` sums the gate's token usage across queries (zeros when no gating ran).

---

### [POST] `/v3/retrieval-signals`

**Contract: identical to `/v2`.** Same request body (with `/v2`'s optional `actions`), same envelope, same per-query block (`id`, `text`, `record_types[{name, coding}]`, `temporal[{name, formula, coding}]`, `tags`), same `details`. Nothing is added. Temporal signals are **generated** by the same contextual-environment Task 1 and Section A/B instructions; record types exactly as `/v2`. What changes is only how a temporal signal is **matched** to the vocabulary:

| | `/v1`, `/v2` | `/v3` |
|---|---|---|
| Temporal vocabulary in the prompt | the whole list (≈1,500 `[id, name]` rows, ≈70K chars) | none — indexed in-process at startup |
| Who finds the vocabulary entry | the model, scanning the list for the entry that matches its signal | code: the model states the span its signal means (`window {relation, value, unit}`), the index returns the entry with that formula |
| Signal not exactly in the vocabulary | the model picks "the nearest broader entry, never narrower" (prompt rule) | code applies the same rule: the narrowest vocabulary window that contains the span, else the widest the vocabulary has |
| Query with no time expression | the model matches the type-default qualifier to an entry | the model states the span that qualifier means for that candidate; code resolves it as above |
| Output `temporal` | `/v1`: one object, the first non-default window in pipeline order. `/v2`: the list of windows described above | **the `/v2` list**: one entry per distinct window, each a vocabulary name, its CUIs and its `[start, end]` formula |

The `window` / `basis` / `rationale` the model produces are matching aids: they are logged (`Temporal inference`, one event per resolved window, with the vocabulary name and CUI chosen) and never returned. Shadow mode and timing are logged the same way (`Temporal shadow comparison`, `Temporal shadow usage`, `Retrieval signals request timing`).

Failure specific to `/v3`: `503` with `error: "temporal_index_unavailable"` when the temporal vocabulary could not be indexed at startup.

#### Request / Response

Exactly as `/v2/retrieval-signals` above; a `/v1` client can call `/v3` without changing its request or its parsing.

---

### Additional examples (v1)

**Example 1: Simplified endpoint (`/v1/extract-intents`)**

```python
import requests

payload = {"texts": "Patient with DM, SOB on exertion"}

response = requests.post(
    "http://localhost:8000/v1/extract-intents",
    headers={"Content-Type": "application/json"},
    json=payload,
)

print(response.json())
```

**Response:**
```json
{
  "status": 1,
  "output": {
    "total_inputs": 1,
    "results": [
      {
        "input_index": 0,
        "original_query": "Patient with DM, SOB on exertion",
        "intents": [
          {
            "intent": "Diabetes Mellitus",
            "intent_description": "Patient has diabetes mellitus condition"
          },
          {
            "intent": "Shortness of Breath",
            "intent_description": "Patient experiencing shortness of breath on exertion"
          }
        ]
      }
    ]
  },
  "details": {
    "timing": {
      "total_llm_seconds": 15.867464,
      "max_llm_seconds": 15.867464,
      "max_llm_seconds_input_index": 0
    },
    "usage_metadata": {
      "query_expansion": {
        "prompt_token_count": 120,
        "candidates_token_count": 45,
        "total_token_count": 165,
        "thinking_token_count": 0
      },
      "intent_extraction": {
        "prompt_token_count": 450,
        "candidates_token_count": 280,
        "total_token_count": 730,
        "thinking_token_count": 0
      }
    },
    "version": "1.0.0",
    "timestamp": "2026-01-14T19:40:20.712694",
    "source": null
  },
  "service": "intent-nature-breakdown-api-1.0.0"
}
```

**Example 2: Full breakdown endpoint (`/v1/nature-breakdown`)**

```python
payload = {
    "texts": [
        "Patient with diabetes mellitus",
        "Current medication: metformin 500mg twice daily",
    ]
}

response = requests.post(
    "http://localhost:8000/v1/nature-breakdown",
    headers={"Content-Type": "application/json"},
    json=payload,
)

print(response.json())
```

**Response:**
```json
{
  "status": 1,
  "output": {
    "total_inputs": 2,
    "results": [
      {
        "input_index": 0,
        "original_query": "Patient with diabetes mellitus",
        "expanded_query": "Patient with diabetes mellitus",
        "abbreviations_expanded": [],
        "is_clinical": true,
        "intents": [
          {
            "intent_title": "Diabetes Mellitus",
            "description": "This intent captures information related to the chronic metabolic condition, Diabetes Mellitus, which affects how the body uses blood sugar.",
            "nature": "Condition / Diagnosis",
            "sub_natures": [
              {
                "category_path": "Condition >> Type",
                "atomic_concepts": [
                  "Diabetes Mellitus"
                ]
              }
            ],
            "final_queries": [
              "Diabetes Mellitus"
            ]
          }
        ]
      },
      {
        "input_index": 1,
        "original_query": "Current medication: metformin 500mg twice daily",
        "expanded_query": "Current medication: metformin 500mg twice daily",
        "abbreviations_expanded": [],
        "is_clinical": true,
        "intents": [
          {
            "intent_title": "Metformin Medication",
            "description": "This intent captures information related to the medication metformin, including dosage and frequency.",
            "nature": "Medication / Treatment",
            "sub_natures": [
              {
                "category_path": "Medication >> Dosage",
                "atomic_concepts": [
                  "metformin",
                  "500mg"
                ]
              },
              {
                "category_path": "Medication >> Frequency",
                "atomic_concepts": [
                  "twice daily"
                ]
              }
            ],
            "final_queries": [
              "metformin 500mg",
              "twice daily"
            ]
          }
        ]
      }
    ]
  },
  "details": {
    "timing": {
      "total_llm_seconds": 15.425826,
      "max_llm_seconds": 7.712913,
      "max_llm_seconds_input_index": 0
    },
    "usage_metadata": {
      "query_expansion": {
        "prompt_token_count": 240,
        "candidates_token_count": 90,
        "total_token_count": 330,
        "thinking_token_count": 0
      },
      "intent_extraction": {
        "prompt_token_count": 900,
        "candidates_token_count": 560,
        "total_token_count": 1460,
        "thinking_token_count": 0
      }
    },
    "version": "1.0.0",
    "timestamp": "2026-01-16T17:55:06.018337",
    "source": null
  },
  "service": "intent-nature-breakdown-api-1.0.0"
}
```

**Example 3: Non-clinical query (both endpoints)**

```python
payload = {
    "texts": "PLEASE JOIN THE MEETING TO LEARN MORE ABOUT HOW THIS CAN HELP IMPROVE YOUR WORKFLOW AND PRODUCTIVITY."
}

response = requests.post(
    "http://localhost:8000/v1/nature-breakdown",
    headers={"Content-Type": "application/json"},
    json=payload,
)

print(response.json())
```

**Response:**
```json
{
  "status": 1,
  "output": {
    "total_inputs": 1,
    "results": [
      {
        "input_index": 0,
        "original_query": "PLEASE JOIN THE MEETING TO LEARN MORE ABOUT HOW THIS CAN HELP IMPROVE YOUR WORKFLOW AND PRODUCTIVITY.",
        "expanded_query": "PLEASE JOIN THE MEETING TO LEARN MORE ABOUT HOW THIS CAN HELP IMPROVE YOUR WORKFLOW AND PRODUCTIVITY.",
        "intents": [],
        "is_clinical": false,
        "rejected_reason": "Query is not clinical in nature"
      }
    ]
  },
  "details": {
    "timing": {
      "total_llm_seconds": 1.552227,
      "max_llm_seconds": 1.552227,
      "max_llm_seconds_input_index": 0
    },
    "usage_metadata": {
      "query_expansion": {
        "prompt_token_count": 100,
        "candidates_token_count": 40,
        "total_token_count": 140,
        "thinking_token_count": 0
      },
      "intent_extraction": {
        "prompt_token_count": 200,
        "candidates_token_count": 60,
        "total_token_count": 260,
        "thinking_token_count": 0
      }
    },
    "version": "1.0.0",
    "timestamp": "2026-01-20T15:58:02.197652",
    "source": null
  },
  "service": "intent-nature-breakdown-api-1.0.0"
}
```

**Example 4: Invalid request format (error handling)**

```python
# Incorrect: Using "text" instead of "texts"
payload = {"text": "I am a care team member looking to identify the 4% AHI value"}

response = requests.post(
    "http://localhost:8000/v1/extract-intents",
    headers={"Content-Type": "application/json"},
    json=payload,
)

print(response.status_code)  # 422
print(response.json())
```

**Error Response:**
```json
{
  "detail": [
    {
      "type": "missing",
      "loc": [
        "body",
        "texts"
      ],
      "msg": "Field required",
      "input": {
        "text": "I am a care team member looking to identify the 4% AHI value (Apnea-Hypopnea Index) from a patient's outside documents. I am interested in this value if it comes from a source other than a home sleep apnea test."
      }
    }
  ]
}
```

**Common Request Errors:**
- Missing `texts` field: Returns 422 with validation error
- Empty `texts` array: Returns 400 with "No input text provided" error
- Invalid JSON: Returns 422 with JSON parsing error
- Invalid `location` (not `us` or `us-central1`): Returns 422 with validation error
- Downstream model/location unavailable: Returns 404 (see [Error Responses](#error-responses) for the full status-code table)

**Example 5: Custom model and generation config**

```python
payload = {
    "texts": "Patient with severe chest pain radiating to left arm",
    "model_name": "gemini-2.5-flash",
    "generation_config": {"temperature": 0.1, "top_p": 0.9, "max_output_tokens": 4096},
}

response = requests.post(
    "http://localhost:8000/v1/extract-intents",
    headers={"Content-Type": "application/json"},
    json=payload,
)
```

**Example 7: Multiple texts with paragraphs**

```python
payload = {
    "texts": [
        "Patient with DM and SOB",
        """45-year-old male presents with acute onset abdominal pain, 
worse with movement. No prior episodes. 
Concern for appendicitis.""",
        """Current medications include:
- Metformin 500mg twice daily for diabetes management
- Lisinopril 10mg daily for hypertension""",
    ]
}

response = requests.post(
    "http://localhost:8000/v1/nature-breakdown",
    headers={"Content-Type": "application/json"},
    json=payload,
)
```

**Note:** For multiple inputs:
- `details.timing.total_llm_seconds` = sum of all processing times
- `details.timing.max_llm_seconds` = maximum processing time across all inputs
- `details.timing.max_llm_seconds_input_index` = index of the input that took longest
- `details.usage_metadata` = aggregated token counts across all inputs
