# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog],
and this project adheres to [Semantic Versioning].

Entries are written per endpoint, from the caller's and the operator's point of
view: request, response, behaviour, errors, configuration. `/v1` endpoints are never
changed by a later version.

## [1.5.12] - 2026-09-24

Retrieval signals: `/v3` added; `/v2` completed (record types through the link-code
pipeline, every temporal window in the response, no error responses caused by the
coding dependencies). All other endpoints unchanged.

### POST /v3/retrieval-signals — new

Same request, same response and the same per-query block as `/v1` and `/v2`. `/v3` is
`/v2` plus a different way of **matching** the temporal window; nothing is removed and
nothing is added to the schema.

- **Request** — as `/v2`: `queries[]` (each `text[]`, optional `record_types`,
  `temporal`, `tags` hints), optional `actions` + `enable_action_finder` (Action Finder
  gate), `model_name`, `generation_config`, `location`.
- **Response** — as `/v1`: `status`, `output.total_queries`, `output.queries[]` with
  `id`, `text`, `record_types[] {name, coding[] {code}}`,
  `temporal {name, formula[], coding[] {system, code}}`, `tags[] {name, coding[], topics[]}`
  (or the `/v2` skip block for a gated-out query), `details {timing, usage_metadata,
  version, timestamp, source, model, location}`, `service`.
- **Temporal matching** — the temporal vocabulary is no longer sent to the model. The
  model states each window as a span (`last 3 years`, `between 6 and 12 months`) and the
  service resolves it against the vocabulary indexed at startup: the entry with that
  exact span, else the narrowest vocabulary window that contains it, else the widest one.
  The result is always a vocabulary entry (its name, CUIs and formula) — the same
  `temporal` values `/v1` produces, chosen by code instead of by the model reading a list.
  A window is resolved **per candidate** of every intent, so a query with no time wording
  still gets the window its clinical nature implies for each candidate.
- **Record types** — as `/v2` (names from the `/v1` prompt; coding through the link-code
  pipeline, below).
- **Errors** — as `/v2`, plus `503 temporal_index_unavailable` when the temporal
  vocabulary could not be indexed at startup (answered before any model call; `/v1` and
  `/v2` keep working).
- **Operations** — per request: `Temporal inference` (one per resolved window: the span
  the model stated, the vocabulary entry chosen, how it was resolved), `Temporal windows
  merged`, `Temporal default applied` (WARNING, only when nothing could be inferred),
  `Retrieval signals request timing`. `TEMPORAL_SHADOW_MODE=true` also runs the `/v2`
  matching and logs where the two disagree (`Temporal shadow comparison`), never
  returned.

### POST /v2/retrieval-signals — changed since 1.4.0

- **Record-type coding is the link-code pipeline.** For each record type the model named
  (names generated exactly as `/v1`), with the query's tag names as context: cluster
  selection → candidate activities of the probable documents → activity selection
  (transaction-selection service, Yes/No) → documents of the selected activities →
  document selection → document classes of the selected documents → class selection.
  The selected classes' node ids are the CUIs, without their row suffix
  (`C3166217-0` → `C3166217`; one code per concept). When a stage selects nothing the
  record type continues through the direct cluster → document-catalog match (exact,
  folded, abbreviation-aware — `PRES. record` codes as a prescription record), the
  `/v1` vocabulary match, and finally a model match against the cluster's document
  classes, so a record type is blank only when nothing anywhere can code it (logged
  `Record types uncoded after every stage`).
- **Every temporal window is in the response.** The nature breakdown resolves a window
  per intent / candidate; the single `temporal` object now carries all of them:
  `name` / `formula` of the window that contains the others (the widest), `coding` = the
  CUIs of every distinct window, the containing window's first. One window is exactly
  the `/v1` value. A client `temporal` hint still overrides.
- **Coding dependencies never produce an error response.** A cluster-selection,
  transaction-selection or BigQuery failure during a request is logged (`ERROR`) and the
  affected record types carry what the remaining stages could code; the response stays
  `200` in the normal shape. `503 cluster_service_unavailable` is now only answered when
  the dependency is not configured, before any model call.
- **Bounded time.** Coding all record types of a query is budgeted
  (`RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS`, 120 s): every service call is capped by
  the time left, no retry sleeps past it, the pipeline stops where it is when the budget
  is spent (classes already confirmed are kept) and the remaining stages code the rest.
  Per-call timeouts: transaction selection 60 s, cluster selection 120 s.
- **Signals are asked for twice before being given up.** The contextual-environment
  answer that produces record types and temporal windows is retried once when it cannot
  be parsed; only two consecutive failures return a query with empty `record_types` and
  the default window.
- **Unchanged since 1.4.0:** request and response schemas, the Action Finder gate and
  its skip block, `details.usage_metadata.action_finder`.

### GET /v2/retrieval-signals/action-types — unchanged since 1.4.0

### POST /v1/retrieval-signals, /v1/nature-breakdown, /v2/nature-breakdown, /v1/extract-intents — unchanged

Same request, response, prompts and vocabularies as 1.3.0.

### Configuration

- `config/.env.<env>`: plain `KEY=VALUE` per environment; `app/config.py` holds every
  setting with its default. The record-type coding settings default to the link-code
  pipeline's own values (project `aif-usr-p-ep-cg-wcdm-0767`, dataset
  `aif_wcdm_osm_pipeline`, `US`; tables `SCENARIO_6_ACTIVITY_CODE_DETAILS_ARRAY`,
  `SCENARIO_6_MEMBER`, `SCENARIO_6_MAP`, `SCENARIO_6_CLUSTER`, `all_results`; the
  cluster-selection and transaction-selection service URLs; cluster set
  `loinc_document_v001`; `gemini-2.5-flash`, batches of 25, 30 workers), so `/v2` and
  `/v3` run without anything being set. A blank value in an env file keeps the default.
- New settings: `TRANSACTION_SELECTION_URL`, `TRANSACTION_SELECTION_MODEL`,
  `TRANSACTION_BATCH_SIZE`, `TRANSACTION_MAX_WORKERS`, `TRANSACTION_MAX_RETRIES`,
  `TRANSACTION_RETRY_DELAY_SECONDS`, `TRANSACTION_REQUEST_TIMEOUT_SECONDS`,
  `RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS`, `RECORD_TYPE_RESOLUTION_MODE`
  (`auto` | `transactions` | `cluster`), `CLUSTER_ACTIVITY_TABLE`,
  `CLUSTER_ALL_RESULTS_TABLE` (+ `_DOC_COLUMN`, `_CLASS_COLUMN`, `_CODE_COLUMN`),
  `CLUSTER_DOCUMENT_CATALOG_TTL_SECONDS`, `CLUSTER_MEMBER_MATCH_MIN_SIMILARITY`,
  `CLUSTER_MEMBER_MATCH_MIN_TOKEN_SCORE`, `CLUSTER_ALIAS_TEXTS_MAX`,
  `CLUSTER_SELECTION_MAX_WORKERS`, `CLUSTER_MEMBER_NAME_KEYS`,
  `RECORD_TYPE_JSON_FALLBACK`, `RECORD_TYPE_LLM_FALLBACK` (+ `_MODEL`, `_MAX_LABELS`),
  `GCLOUD_PATH`; `/v3`: `TEMPORAL_INDEX_SIMILARITY_PROVIDER`, `TEMPORAL_EMBEDDING_MODEL`,
  `TEMPORAL_INDEX_CACHE_DIR`, `TEMPORAL_MAX_CODES_PER_WINDOW`, `TEMPORAL_MIN_SIMILARITY`,
  `TEMPORAL_MENU_MAX_VALUES`, `TEMPORAL_MENU_UNITS`, `TEMPORAL_SHORTLIST_TOP_N`,
  `TEMPORAL_SHORTLIST_MIN_SCORE`, `TEMPORAL_SHADOW_MODE`, `CADENCE_MEMORY_*`
  (optional, off when blank).
- Service account: invoker on the cluster-selection and transaction-selection services,
  BigQuery Data Viewer + Job User on the dataset (in addition to 1.3.0's Vertex AI and
  GCS access). Locally, identity tokens come from `gcloud auth print-identity-token`
  (`ALLOW_LOCAL_GCLOUD_AUTH`, default on).
- Request time in front of the service: a `/v2` or `/v3` request is the model steps
  plus at most the record-type budget; keep the Cloud Run / gunicorn timeout (300 s)
  above that.

### Operations

- Startup: `Temporal index built` (entries, distinct windows, unit codes),
  `Document-cluster record-type resolution configured` (URLs, project, dataset, budget),
  `Document catalog loaded` (background warm-up).
- Per request: `Record-type transaction pipeline` (stage counts, outcome, seconds, one
  per record type), `Temporal inference`, `Temporal windows merged`,
  `Retrieval signals request timing` (`total_llm_seconds`,
  `record_type_resolution_seconds`), `Vocabulary coverage`.
- Warnings / errors worth an alert: `Record types uncoded after every stage`,
  `Temporal default applied`, `Contextual environment … returning empty context`,
  `Cluster selection failed…`, `Document-class lookup failed…`,
  `Record-type transaction pipeline failed`.

## [1.4.0] - 2026-09-23

### POST /v2/retrieval-signals — new

`/v1/retrieval-signals` plus an optional Action Finder gate and record-type coding
through the LOINC document cluster; same response schema as `/v1`.

- **Request** — `/v1` body plus `actions` (list of `conversational_response`,
  `information_retrieval`, `knowledge_fact_search`, `admin_response`, `out_of_scope`)
  and `enable_action_finder` (default `true`). Omitting `actions`, or an empty list, means
  no gating.
- **Gate** — with `actions`, each query is classified in-process first; signals are
  computed only when its identified actions overlap `actions` and include
  `information_retrieval`. Otherwise the query's entry is a skip block:
  `{id, text, skipped: true, skip_reason: [identified actions],
  skip_reason_detail, record_types: [], temporal: null, tags: []}`. A classifier failure
  fails open (the query is processed) and is logged.
- **Record types** — CUIs resolved per request through the document cluster (name + tag
  names as context → cluster-selection service → member documents → BigQuery document
  classes) instead of the `record_type_name_to_cui.json` vocabulary.
- **Response** — `/v1` envelope; `details.usage_metadata` gains `action_finder`.
- **Errors** — `422 validation_error` for an unknown action; `503
  cluster_service_unavailable` when the cluster dependency is not configured.

### GET /v2/retrieval-signals/action-types — new

Returns `{enable_action_finder: true, actions: [...]}` — the values accepted in
`actions`.

### Unchanged

`/v1/retrieval-signals`, `/v1/nature-breakdown`, `/v2/nature-breakdown`,
`/v1/extract-intents`.

### Configuration

`CLUSTER_SELECTION_URL`, `CLUSTER_BQ_PROJECT`, `CLUSTER_BQ_DATASET`,
`CLUSTER_BQ_LOCATION`, `CLUSTER_MEMBER_TABLE`, `CLUSTER_MAP_TABLE`,
`CLUSTER_CLUSTER_TABLE`, `CLUSTER_SET`, `CLUSTER_TOP_K`, `CLUSTER_TOP_P`,
`CLUSTER_COMBINED`, `CLUSTER_REQUEST_TIMEOUT_SECONDS`, `ALLOW_LOCAL_GCLOUD_AUTH`,
`ACTION_FINDER_MODEL`; `google-cloud-bigquery` dependency.

## [1.0.0] - 2023-07-15

- initial changelog added, changes before this would require looking at git logs

<!-- Links -->
[keep a changelog]: https://keepachangelog.com/en/1.0.0/
[semantic versioning]: https://semver.org/spec/v2.0.0.html
