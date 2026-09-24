"""Record-type coding for /v2 and /v3 retrieval signals: the standalone
link-code pipeline, called per record type.

For each record type the model named, with the query's tag names as context,
the service runs exactly what the standalone script's process_search_pair
runs (see document_transaction_pipeline): cluster selection -> activities ->
Stage 1 -> related documents -> Stage 2 -> document classes -> Stage 3; the
selected classes' node ids are the CUIs. When a step finds nothing the record
type has no codes, as the script records the pair without classes — there is
no other source of codes.

This module holds what the pipeline needs from the service side: the
identity tokens for the two services, the cluster-selection call, the
BigQuery client and the per-request resolver (`resolve_record_type_cuis`).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request
from google.oauth2 import id_token

from app import config
from app.exceptions import ClusterServiceError

# Identity tokens are valid for one hour; refresh comfortably before that.
_TOKEN_TTL_SECONDS = 50 * 60
# Record types of one query are coded concurrently, each through its own
# pipeline run (CLUSTER_SELECTION_MAX_WORKERS).
_CLUSTER_SELECTION_MAX_WORKERS = 8

_auth_lock = threading.Lock()
_auth_state: dict[str, Any] = {"token": None, "fetched_at": 0.0}
_bq_client = None
_bq_lock = threading.Lock()

Resolver = Callable[[str, str | None], list[dict[str, Any]]]

# Diagnostics: the service logger is injected at startup (set_logger); until
# then, or outside the app, a stdlib logger receives the same events.
_std_logger = logging.getLogger(__name__)
_diag: dict[str, Any] = {"lg": None, "base_log": {}}


def set_logger(lg: Any, base_log: dict[str, Any] | None = None) -> None:
    _diag["lg"] = lg
    _diag["base_log"] = dict(base_log or {})


def _log(message: str, data: dict[str, Any], severity: str = "WARNING") -> None:
    lg = _diag["lg"]
    if lg is not None:
        try:
            lg.log_struct(
                message=message,
                structured_data={**_diag["base_log"], **data},
                severity=severity,
            )
            return
        except Exception as log_err:  # noqa: BLE001 — diagnostics never break resolution
            _std_logger.debug("structured logger failed: %s", log_err)
    _std_logger.log(
        logging.getLevelName(severity) if isinstance(severity, str) else severity,
        "%s %s",
        message,
        json.dumps(data, ensure_ascii=False, default=str)[:2000],
    )


def _member_name_keys() -> tuple[str, ...]:
    raw = getattr(config, "CLUSTER_MEMBER_NAME_KEYS", "member_name") or "member_name"
    return tuple(k.strip() for k in str(raw).split(",") if k.strip())


# ---------------------------------------------------------------------------
# Configuration guards
# ---------------------------------------------------------------------------
def is_configured() -> bool:
    """True when the two service URLs and the BigQuery dataset are set."""
    return not missing_configuration()


def missing_configuration() -> list[str]:
    return [
        name
        for name in (
            "CLUSTER_SELECTION_URL",
            "TRANSACTION_SELECTION_URL",
            "CLUSTER_BQ_DATASET",
        )
        if not getattr(config, name, "")
    ]


def _require_configured() -> None:
    missing = missing_configuration()
    if missing:
        raise ClusterServiceError(
            "Document-cluster service is not configured",
            {"missing_settings": missing},
        )


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
def _fetch_identity_token(audience: str | None = None) -> str | None:
    """Identity token for ``audience`` (default: the cluster-selection URL),
    or None when no credential source is available (the caller then sends no
    Authorization). Cloud Run tokens are audience-specific, so each service
    URL gets its own."""
    audience = audience or config.CLUSTER_SELECTION_URL
    try:
        # Cloud Run and other GCP runtimes provide identity credentials through
        # the metadata server / attached service account.
        return id_token.fetch_id_token(Request(), audience) or None
    except DefaultCredentialsError:
        if not config.ALLOW_LOCAL_GCLOUD_AUTH:
            return None

    # Local runs use the gcloud CLI, as the pipeline script does: the
    # configured path, else gcloud on PATH. A service-account login can mint
    # an audience-bound token; a user login cannot (`--audiences` is refused),
    # so the plain identity token the script uses is the fallback.
    gcloud = (getattr(config, "GCLOUD_PATH", "") or "").strip() or shutil.which(
        "gcloud"
    )
    if not gcloud:
        return None
    for extra in ([f"--audiences={audience}"], []):
        try:
            result = subprocess.run(
                [gcloud, "auth", "print-identity-token", *extra],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return None
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def get_auth_headers(force_refresh: bool = False) -> dict[str, str]:
    """Request headers for the cluster-selection service.

    The identity token is cached and refreshed before it expires (or when the
    caller reports it was rejected). Without a token only Content-Type is sent.
    """
    with _auth_lock:
        now = time.monotonic()
        stale = (now - _auth_state["fetched_at"]) > _TOKEN_TTL_SECONDS
        if force_refresh or stale or _auth_state["token"] is None:
            _auth_state["token"] = _fetch_identity_token()
            _auth_state["fetched_at"] = now
        token = _auth_state["token"]

    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


_audience_tokens: dict[str, dict[str, Any]] = {}


def get_auth_headers_for(audience: str, force_refresh: bool = False) -> dict[str, str]:
    """Request headers for another service URL (the transaction-selection
    service): one cached identity token per audience, refreshed before expiry
    or when the caller reports a rejection."""
    if not audience or audience == config.CLUSTER_SELECTION_URL:
        return get_auth_headers(force_refresh=force_refresh)
    with _auth_lock:
        state = _audience_tokens.setdefault(
            audience, {"token": None, "fetched_at": 0.0}
        )
        now = time.monotonic()
        stale = (now - state["fetched_at"]) > _TOKEN_TTL_SECONDS
        if force_refresh or stale or state["token"] is None:
            state["token"] = _fetch_identity_token(audience)
            state["fetched_at"] = now
        token = state["token"]
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def reset_auth_cache() -> None:
    """Drop the cached identity tokens (tests / credential rotation)."""
    with _auth_lock:
        _auth_state["token"] = None
        _auth_state["fetched_at"] = 0.0
        _audience_tokens.clear()


# ---------------------------------------------------------------------------
# Cluster selection
# ---------------------------------------------------------------------------
def get_context_text(
    search_document_name: str, search_context: str | None = None
) -> str:
    """Tag context when present, otherwise the record-type name itself."""
    if search_context and search_context.strip():
        return search_context.strip()
    return search_document_name


def call_cluster_selection(
    search_document_name: str,
    search_context: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """POST one record-type name to the cluster-selection service.

    Returns the raw JSON response, or {} when no identity token is available
    or the service rejects the token (a not-authorised caller gets no codes,
    not an error). Any other non-200 answer raises ClusterServiceError.
    ``timeout`` overrides CLUSTER_REQUEST_TIMEOUT_SECONDS (a caller working
    against a deadline passes the time it has left).
    """
    _require_configured()
    headers = get_auth_headers()
    if not headers.get("Authorization"):
        return {}

    payload = {
        "cluster_set": config.CLUSTER_SET,
        "text_list": [search_document_name],
        "context_list": [get_context_text(search_document_name, search_context)],
        "top_k": config.CLUSTER_TOP_K,
        "top_p": config.CLUSTER_TOP_P,
        "combined": config.CLUSTER_COMBINED,
    }

    def _post(hdrs: dict[str, str]) -> requests.Response:
        try:
            return requests.post(
                config.CLUSTER_SELECTION_URL,
                json=payload,
                headers=hdrs,
                timeout=(
                    config.CLUSTER_REQUEST_TIMEOUT_SECONDS
                    if timeout is None
                    else max(1.0, float(timeout))
                ),
            )
        except requests.RequestException as e:
            raise ClusterServiceError(
                "Cluster-selection request failed",
                {"error_type": type(e).__name__, "error": str(e)},
            ) from e

    response = _post(headers)
    if response.status_code in (401, 403):
        # Token may have expired between refreshes — retry once with a new one.
        headers = get_auth_headers(force_refresh=True)
        if not headers.get("Authorization"):
            return {}
        response = _post(headers)
        if response.status_code in (401, 403):
            return {}
    if response.status_code != 200:
        raise ClusterServiceError(
            "Cluster-selection service returned an error",
            {"status_code": response.status_code, "response": response.text[:500]},
        )
    try:
        return response.json() or {}
    except ValueError as e:
        raise ClusterServiceError(
            "Cluster-selection service returned invalid JSON",
            {"error": str(e)},
        ) from e


def extract_member_names(obj: Any, keys: tuple[str, ...] | None = None) -> list[str]:
    """Every non-empty member-name value anywhere in the response, sorted.
    The key(s) holding the name default to ``member_name`` and can be set
    with ``CLUSTER_MEMBER_NAME_KEYS`` (comma-separated) when the service
    answers with a different field name."""
    member_names: set[str] = set()
    wanted = keys or _member_name_keys()

    def recursive_extract(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in wanted:
                    if isinstance(child, str):
                        name = child.strip()
                        if name:
                            member_names.add(name)
                    elif isinstance(child, list):
                        for item in child:
                            if isinstance(item, str) and item.strip():
                                member_names.add(item.strip())
                recursive_extract(child)
        elif isinstance(value, list):
            for item in value:
                recursive_extract(item)

    recursive_extract(obj)
    return sorted(member_names)


def get_probable_documents(
    search_document_name: str,
    search_context: str | None = None,
    timeout: float | None = None,
) -> list[str]:
    """Probable member document names for one record-type name."""
    response = call_cluster_selection(search_document_name, search_context, timeout)
    names = extract_member_names(response)
    if not names and response:
        # The service answered but nothing under the configured member-name
        # key: log the shape so a field-name mismatch is visible at once.
        _log(
            "Cluster selection returned no member names",
            {
                "record_type": search_document_name,
                "member_name_keys": list(_member_name_keys()),
                "response_top_level_keys": (
                    sorted(response.keys()) if isinstance(response, dict) else None
                ),
                "response_preview": json.dumps(response, ensure_ascii=False)[:400],
            },
        )
    return names


# ---------------------------------------------------------------------------
# BigQuery lookup
# ---------------------------------------------------------------------------
def get_bq_client():
    """Lazily built, process-wide BigQuery client."""
    global _bq_client
    with _bq_lock:
        if _bq_client is None:
            try:
                from google.cloud import bigquery
            except ImportError as e:  # pragma: no cover - dependency guard
                raise ClusterServiceError(
                    "google-cloud-bigquery is not installed", {"error": str(e)}
                ) from e
            _bq_client = bigquery.Client(
                project=config.CLUSTER_BQ_PROJECT, location=config.CLUSTER_BQ_LOCATION
            )
        return _bq_client


def _normalize_record_types(record_types: Iterable[Any] | None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for entry in record_types or []:
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = str(entry.get("name") or entry.get("record_type") or "").strip()
        else:
            continue
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _context_from_tags(tags: Iterable[Any] | None) -> str:
    context_names: list[str] = []
    for tag in tags or []:
        if isinstance(tag, str):
            text = tag.strip()
        elif isinstance(tag, dict):
            text = str(tag.get("name") or tag.get("text") or "").strip()
        else:
            text = ""
        if text and text not in context_names:
            context_names.append(text)
    return ", ".join(context_names)


def _coding_from_hits(hits: Iterable[dict[str, Any]] | None) -> list[dict[str, str]]:
    coding: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in hits or []:
        for code in hit.get("coding") or []:
            code_value = str(code.get("code", "")).strip()
            if code_value and code_value not in seen:
                seen.add(code_value)
                coding.append({"code": code_value})
    return coding


def resolve_record_type_cuis(
    record_types: Iterable[Any] | None,
    tags: Iterable[Any] | None = None,
    resolver: Resolver | None = None,
) -> list[dict[str, Any]]:
    """Code record-type objects exactly as the standalone pipeline codes a
    (text, context) pair: one pipeline run per record type, its name as the
    search document name and the query's tag names (comma-joined) as the
    context.

    Input:  [{"name": "Radiology Report", "coding": []}, ...] (or bare strings)
    Tags:   [{"name": "chest X-ray"}, {"name": "imaging"}] (or bare strings)
    Output: [{"name": "Radiology Report", "coding": [{"code": "C..."}]}, ...]

    The coding is the ordered, de-duplicated list of node ids of the classes
    Stage 3 selected. A record type for which a step found nothing — or whose
    run failed or ran out of the resolution budget — has an empty coding and
    a log line saying which step stopped it; nothing else codes it. Record
    types run concurrently. `resolver(name, context)` can be injected (tests).
    """
    names = _normalize_record_types(record_types)
    if not names:
        return []
    context = _context_from_tags(tags)

    if resolver is not None:
        return [
            {"name": name, "coding": _coding_from_hits(resolver(name, context))}
            for name in names
        ]

    from app.utils import document_transaction_pipeline as tx

    budget = float(getattr(config, "RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS", 210) or 0)
    deadline = time.monotonic() + budget if budget > 0 else None

    workers = max(
        1,
        min(
            int(
                getattr(
                    config,
                    "CLUSTER_SELECTION_MAX_WORKERS",
                    _CLUSTER_SELECTION_MAX_WORKERS,
                )
                or 1
            ),
            len(names),
        ),
    )
    with ThreadPoolExecutor(max_workers=workers) as executor:
        hits_by_name = dict(
            zip(
                names,
                executor.map(lambda n: _pipeline_for(tx, n, context, deadline), names),
                strict=True,
            )
        )
    return [
        {"name": name, "coding": _coding_from_hits(hits_by_name.get(name))}
        for name in names
    ]


def _pipeline_for(
    tx: Any, name: str, context: str | None, deadline: float | None = None
) -> list[dict[str, Any]]:
    """The pipeline for one record type. A failure is logged and leaves the
    record type uncoded — it never fails the request."""
    left = tx.remaining(deadline)
    if left is not None and left <= 0:
        _log(
            "Record-type transaction pipeline skipped: resolution deadline reached",
            {"record_type": name},
        )
        return []
    try:
        return tx.resolve_via_transactions(name, context, deadline=deadline)
    except Exception as e:  # noqa: BLE001 — the pipeline never fails the request
        details = getattr(e, "details", None) or {}
        _log(
            "Record-type transaction pipeline failed; record type left uncoded",
            {
                **details,
                "record_type": name,
                "error_type": type(e).__name__,
                "error": getattr(e, "message", str(e)),
            },
            severity="ERROR",
        )
        return []
