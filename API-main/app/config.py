import os

#  For local development purposes, load environment variables from .env files
# Comment or remove these lines two while pushing to repository
# from dotenv import load_dotenv

# load_dotenv(
#     dotenv_path=os.path.abspath(
#         os.path.join(os.path.dirname(__file__), "..", "config", ".env.dev")
#     )
# )


ALLOWED_GEN_MODELS = [
    "gemini-3.8-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
]
DEFAULT_GEN_MODEL = ALLOWED_GEN_MODELS[4]

ENV = os.getenv("ENV", "dev").strip()
PROJECT_ID = os.getenv("PROJECT_ID").strip()
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
MODEL_VERSION = os.getenv("MODEL_VERSION", DEFAULT_GEN_MODEL).strip()

if MODEL_VERSION not in ALLOWED_GEN_MODELS:
    raise ValueError(
        f"MODEL_VERSION {MODEL_VERSION!r} is not in ALLOWED_GEN_MODELS: "
        f"{', '.join(ALLOWED_GEN_MODELS)}"
    )
SERVICE_NAME = "intent-nature-breakdown-api"
SERVICE_VERSION = "1.5.16"
SERVICE_ID = SERVICE_NAME + "-" + SERVICE_VERSION
# Path prefix the app serves under; matches the ILB/URL-map route. Set API_PREFIX=""
# if the load balancer strips the prefix before forwarding.
API_PREFIX = os.getenv("API_PREFIX", "/intent-nature-breakdown").rstrip("/")
os.environ.setdefault("OTEL_SERVICE_NAME", SERVICE_NAME)
# Enabled by default. Set ENABLE_TRACING=false in environment to disable (e.g., for local development)
ENABLE_TRACING = os.getenv("ENABLE_TRACING", "true").lower().strip() not in ("false")
TEST_VARIABLE = os.getenv("TEST_VARIABLE", "default_value").strip()

# Temporal vocabulary for v2 retrieval signals.
# Prefer GCS when TEMPORAL_GCS_BUCKET is set; otherwise load from local file.
TEMPORAL_GCS_BUCKET = os.getenv("TEMPORAL_GCS_BUCKET", "").strip()
TEMPORAL_GCS_PATH = os.getenv(
    "TEMPORAL_GCS_PATH", "Nature_breakdown/temporal_name_to_cui.json"
).strip()

_DEFAULT_TEMPORAL_LOCAL = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "temporal_name_to_cui.json",
    )
)
TEMPORAL_LOCAL_PATH = os.getenv("TEMPORAL_LOCAL_PATH", _DEFAULT_TEMPORAL_LOCAL).strip()

# Record-type vocabulary for /v1/retrieval-signals.
# Same GCS-first / local-fallback convention as the temporal vocabulary.
RECORD_TYPE_GCS_BUCKET = os.getenv("RECORD_TYPE_GCS_BUCKET", "").strip()
RECORD_TYPE_GCS_PATH = os.getenv(
    "RECORD_TYPE_GCS_PATH", "Nature_breakdown/record_type_name_to_cui.json"
).strip()

_DEFAULT_RECORD_TYPE_LOCAL = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "app",
        "record_type_name_to_cui.json",
    )
)
RECORD_TYPE_LOCAL_PATH = os.getenv(
    "RECORD_TYPE_LOCAL_PATH", _DEFAULT_RECORD_TYPE_LOCAL
).strip()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    """An integer setting; a blank or malformed value (an `X=` placeholder
    left in an env file) means the default rather than a startup failure."""
    raw = os.getenv(name)
    try:
        return int(float(raw.strip())) if raw and raw.strip() else default
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    try:
        return float(raw.strip()) if raw and raw.strip() else default
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# /v2/retrieval-signals — Action Finder gate.
# Optional model override for the in-process Action Finder classification.
# Empty (default) means: use the request's model_name, else MODEL_VERSION.
# ---------------------------------------------------------------------------
ACTION_FINDER_MODEL = os.getenv("ACTION_FINDER_MODEL", "").strip()
if ACTION_FINDER_MODEL and ACTION_FINDER_MODEL not in ALLOWED_GEN_MODELS:
    raise ValueError(
        f"ACTION_FINDER_MODEL {ACTION_FINDER_MODEL!r} is not in ALLOWED_GEN_MODELS: "
        f"{', '.join(ALLOWED_GEN_MODELS)}"
    )


def _env_str(name: str, default: str) -> str:
    """A string setting; a blank value (a `KEY=` line in an env file) means
    the default, not an empty string."""
    raw = os.getenv(name)
    return raw.strip() if raw and raw.strip() else default


# ===========================================================================
# RECORD-TYPE CODING (/v2 and /v3) — the link-code pipeline's configuration.
#
# Same values as the standalone pipeline script (its CONFIGURATION, SOURCE
# TABLES, APIS and PARALLELISM blocks), so nothing needs to be set for it to
# run. Any value can be overridden per environment by the same-named variable
# in config/.env.<env>; a blank value there keeps the default below.
#
# The service account running the API needs invoker on both services and
# BigQuery Data Viewer + Job User on the dataset.
# ===========================================================================
# --- GCP project / BigQuery ---
CLUSTER_BQ_PROJECT = _env_str("CLUSTER_BQ_PROJECT", "aif-usr-p-ep-cg-wcdm-0767")
CLUSTER_BQ_DATASET = _env_str("CLUSTER_BQ_DATASET", "aif_wcdm_osm_pipeline")
CLUSTER_BQ_LOCATION = _env_str("CLUSTER_BQ_LOCATION", "US")

# --- Source tables (in CLUSTER_BQ_PROJECT.CLUSTER_BQ_DATASET) ---
# activities of the probable documents, and the document classes (+ node ids)
CLUSTER_ACTIVITY_TABLE = _env_str(
    "CLUSTER_ACTIVITY_TABLE", "SCENARIO_6_ACTIVITY_CODE_DETAILS_ARRAY"
)
CLUSTER_ALL_RESULTS_TABLE = _env_str("CLUSTER_ALL_RESULTS_TABLE", "all_results")
# columns of the results table: document name, document class, node id
CLUSTER_ALL_RESULTS_DOC_COLUMN = _env_str(
    "CLUSTER_ALL_RESULTS_DOC_COLUMN", "document_type"
)
CLUSTER_ALL_RESULTS_CLASS_COLUMN = _env_str(
    "CLUSTER_ALL_RESULTS_CLASS_COLUMN", "document_class"
)
CLUSTER_ALL_RESULTS_CODE_COLUMN = _env_str("CLUSTER_ALL_RESULTS_CODE_COLUMN", "node_id")

# --- APIs ---
CLUSTER_SELECTION_URL = _env_str(
    "CLUSTER_SELECTION_URL",
    "https://cluster-selection-api-68004442910.us-central1.run.app"
    "/cluster-selection/v2/cui_matching",
)
TRANSACTION_SELECTION_URL = _env_str(
    "TRANSACTION_SELECTION_URL",
    "https://transaction-selection-api-705290722717.us-central1.run.app"
    "/transaction_selection",
)
CLUSTER_SET = _env_str("CLUSTER_SET", "loinc_document_v001")
CLUSTER_TOP_K = max(1, _env_int("CLUSTER_TOP_K", 10))
CLUSTER_TOP_P = max(1, _env_int("CLUSTER_TOP_P", 20))
CLUSTER_COMBINED = _env_bool("CLUSTER_COMBINED", True)
TRANSACTION_SELECTION_MODEL = _env_str(
    "TRANSACTION_SELECTION_MODEL", "gemini-2.5-flash"
)
# --- Parallelism / batching / retries (as the pipeline: 25 per batch, 30 workers) ---
TRANSACTION_BATCH_SIZE = max(1, _env_int("TRANSACTION_BATCH_SIZE", 25))
TRANSACTION_MAX_WORKERS = max(1, _env_int("TRANSACTION_MAX_WORKERS", 30))
TRANSACTION_MAX_RETRIES = max(1, _env_int("TRANSACTION_MAX_RETRIES", 5))
TRANSACTION_RETRY_DELAY_SECONDS = max(
    0.0, _env_float("TRANSACTION_RETRY_DELAY_SECONDS", 3)
)
# Concurrent record types per query (each runs its own pipeline).
CLUSTER_SELECTION_MAX_WORKERS = max(1, _env_int("CLUSTER_SELECTION_MAX_WORKERS", 8))

# --- Time limits (the pipeline runs inside a request here, not offline) ---
# Per cluster-selection call / per transaction-selection call, as the script
# (120 s / 300 s); each call is also capped by what is left of the budget.
CLUSTER_REQUEST_TIMEOUT_SECONDS = max(
    1, _env_int("CLUSTER_REQUEST_TIMEOUT_SECONDS", 120)
)
TRANSACTION_REQUEST_TIMEOUT_SECONDS = max(
    1, _env_int("TRANSACTION_REQUEST_TIMEOUT_SECONDS", 300)
)
# Budget for coding ALL record types of one query (they run concurrently).
# When it runs out the pipeline stops where it is: a record type keeps the
# classes Stage 3 confirmed so far, or none. Together with the model steps it
# must fit inside the request timeout in front of the service (Cloud Run
# default 300 s); raise both together if the logs show "deadline reached".
# 0 = no limit.
RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS = max(
    0.0, _env_float("RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS", 210)
)

# --- Authentication ---
# Cloud Run: identity tokens come from the metadata server (one per service
# URL). Local runs: `gcloud auth print-identity-token`, as the pipeline script
# does. GCLOUD_PATH points at the executable when it is not on PATH.
ALLOW_LOCAL_GCLOUD_AUTH = _env_bool("ALLOW_LOCAL_GCLOUD_AUTH", True)
GCLOUD_PATH = _env_str("GCLOUD_PATH", "")

# Field name(s) carrying member document names in the cluster-selection
# response (comma-separated; searched recursively).
CLUSTER_MEMBER_NAME_KEYS = _env_str("CLUSTER_MEMBER_NAME_KEYS", "member_name")

# ---------------------------------------------------------------------------
# /v3/retrieval-signals — canonical temporal mode.
# The temporal vocabulary is indexed in-process at startup (formula index +
# name similarity) and never pasted into the prompt; the model emits a
# structured window that the index resolves. Every limit below is a setting;
# no vocabulary content or clinical mapping lives in code.
# ---------------------------------------------------------------------------
# Name-similarity provider for the index: "lexical" (in-process character
# n-gram TF-IDF, no network) or "vertex" (Vertex text embeddings; vectors are
# cached on disk keyed by model + vocabulary hash).
TEMPORAL_INDEX_SIMILARITY_PROVIDER = (
    os.getenv("TEMPORAL_INDEX_SIMILARITY_PROVIDER", "lexical").strip().lower()
)
TEMPORAL_EMBEDDING_MODEL = os.getenv("TEMPORAL_EMBEDDING_MODEL", "").strip()
TEMPORAL_INDEX_CACHE_DIR = os.getenv("TEMPORAL_INDEX_CACHE_DIR", "").strip()
# Resolution: distinct CUIs kept per window (ranked by closeness to the
# model's wording) and the similarity floor below which extra entries drop.
TEMPORAL_MAX_CODES_PER_WINDOW = max(1, _env_int("TEMPORAL_MAX_CODES_PER_WINDOW", 2))
TEMPORAL_MIN_SIMILARITY = _env_float("TEMPORAL_MIN_SIMILARITY", 0.3)
# Prompt: codeable-window menu (values per unit, most represented first) and
# the query-time shortlist of vocabulary names close to the query wording.
TEMPORAL_MENU_MAX_VALUES = max(0, _env_int("TEMPORAL_MENU_MAX_VALUES", 12))
TEMPORAL_MENU_UNITS = [
    u.strip().lower()
    for u in os.getenv("TEMPORAL_MENU_UNITS", "day,week,month,year").split(",")
    if u.strip()
]
TEMPORAL_SHORTLIST_TOP_N = max(0, _env_int("TEMPORAL_SHORTLIST_TOP_N", 8))
TEMPORAL_SHORTLIST_MIN_SCORE = _env_float("TEMPORAL_SHORTLIST_MIN_SCORE", 0.3)
# Shadow mode: /v3 also runs the legacy vocabulary-list path and logs
# per-candidate agreement ("Temporal shadow comparison"). Doubles the
# contextual-environment call; for rollout only.
TEMPORAL_SHADOW_MODE = _env_bool("TEMPORAL_SHADOW_MODE", False)

# Cadence memory: the service's own inferred windows aggregated from its logs
# by scripts/build_cadence_memory.py (GCS first, local fallback, optional).
CADENCE_MEMORY_GCS_BUCKET = os.getenv("CADENCE_MEMORY_GCS_BUCKET", "").strip()
CADENCE_MEMORY_GCS_PATH = os.getenv(
    "CADENCE_MEMORY_GCS_PATH", "Nature_breakdown/cadence_memory.json"
).strip()
_DEFAULT_CADENCE_MEMORY_LOCAL = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "app", "cadence_memory.json")
)
CADENCE_MEMORY_LOCAL_PATH = os.getenv(
    "CADENCE_MEMORY_LOCAL_PATH", _DEFAULT_CADENCE_MEMORY_LOCAL
).strip()
CADENCE_MEMORY_EXAMPLES_PER_CONCEPT = max(
    1, _env_int("CADENCE_MEMORY_EXAMPLES_PER_CONCEPT", 3)
)
CADENCE_MEMORY_MAX_CONCEPTS = max(0, _env_int("CADENCE_MEMORY_MAX_CONCEPTS", 10))
CADENCE_MEMORY_MIN_SIMILARITY = _env_float("CADENCE_MEMORY_MIN_SIMILARITY", 0.6)
