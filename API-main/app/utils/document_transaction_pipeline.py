"""Record-type -> document-class CUIs through the transaction-selection pipeline.

This is the standalone "link code" pipeline (process_search_pair) made
callable per record type inside the service. For one search document name
(the record-type label) and its context (the query's tag names):

  1. CLUSTER SELECTION      cluster-selection service -> probable member documents
  2. ACTIVITIES             BigQuery ACTIVITY table -> candidate activities of those documents
  3. STAGE 1 selection      transaction-selection service: is each activity related? (Yes/No)
  4. RELATED DOCUMENTS      BigQuery ACTIVITY table -> documents of the selected activities
  5. STAGE 2 selection      transaction-selection service: is each document related? (Yes/No)
  6. DOCUMENT CLASSES       BigQuery results table -> classes (+ node id) of the selected documents
  7. STAGE 3 selection      transaction-selection service: is each class related? (Yes/No)

The selected classes' node ids, without their per-row suffix ("C3166217-0"
-> "C3166217"), are the CUIs returned for the record type.
Payloads sent to the transaction-selection service are the pipeline's own
(instructions, elements, guidelines, output spec), reproduced as they are;
batch sizes, worker counts, retries and table/column names are settings.
The BigQuery queries are the script's own: names are matched exactly as the
previous step returned them.

The pipeline runs inside a request, so it works against a deadline (the
caller's RECORD_TYPE_RESOLUTION_TIMEOUT_SECONDS budget): every service call
is capped by the time left, no retry sleeps past it, and when it runs out
the pipeline stops and reports how far it got. As in the script, a step that
finds nothing ends the run for that record type with no codes.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import requests

from app import config
from app.exceptions import ClusterServiceError
from app.utils import context_lonic_document_cluster as cluster


# ---------------------------------------------------------------------------
# Settings (all read at call time so tests / rollouts can change them)
# ---------------------------------------------------------------------------
def _setting(name: str, default: Any) -> Any:
    value = getattr(config, name, default)
    return default if value is None else value


def remaining(deadline: float | None) -> float | None:
    """Seconds left before ``deadline`` (a time.monotonic() value); None when
    there is no deadline. Never negative."""
    if deadline is None:
        return None
    return max(0.0, deadline - time.monotonic())


def _capped(timeout: float, deadline: float | None) -> float:
    """``timeout`` capped by the time left; at least one second so a call
    made right before the deadline still gets a real chance."""
    left = remaining(deadline)
    if left is None:
        return timeout
    return max(1.0, min(float(timeout), left))


class DeadlineExceeded(ClusterServiceError):
    """Raised inside the pipeline when the resolution budget is spent."""


def is_enabled() -> bool:
    """The pipeline needs the transaction-selection URL (its stages 1-3)."""
    return bool(str(_setting("TRANSACTION_SELECTION_URL", "")).strip())


def _table(name: str) -> str:
    return f"{config.CLUSTER_BQ_PROJECT}.{config.CLUSTER_BQ_DATASET}.{name}"


def _names(names: Iterable[str]) -> list[str]:
    """Distinct non-empty names, as given (the script matches them exactly)."""
    out: list[str] = []
    for n in names:
        n = str(n or "").strip()
        if n and n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# Transaction-selection service
# ---------------------------------------------------------------------------
def send_transaction_selection(
    payload: dict[str, Any],
    max_retries: int | None = None,
    retry_delay: float | None = None,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """POST one batch to the transaction-selection service and return its
    ``output`` list. Retries an item-count mismatch (400 "incorrect number of
    items"), rate limits and transient server errors, as the pipeline did.
    Every attempt's timeout is capped by the ``deadline``; a retry that
    would start (or sleep) past it raises DeadlineExceeded instead."""
    url = str(_setting("TRANSACTION_SELECTION_URL", "")).strip()
    if not url:
        raise ClusterServiceError(
            "Transaction-selection service is not configured",
            {"missing_settings": ["TRANSACTION_SELECTION_URL"]},
        )
    retries = int(max_retries or _setting("TRANSACTION_MAX_RETRIES", 5))
    delay = float(retry_delay or _setting("TRANSACTION_RETRY_DELAY_SECONDS", 3))
    timeout = int(_setting("TRANSACTION_REQUEST_TIMEOUT_SECONDS", 60))
    last_error: str = ""

    def _pause(seconds: float) -> None:
        # Sleep before the next attempt only when the attempt can still run.
        left = remaining(deadline)
        if left is not None and left <= seconds + 1.0:
            raise DeadlineExceeded(
                "Transaction-selection retry abandoned: resolution deadline reached",
                {"last_error": last_error, "seconds_left": round(left, 1)},
            )
        time.sleep(seconds)

    for attempt in range(1, retries + 1):
        left = remaining(deadline)
        if left is not None and left <= 0:
            raise DeadlineExceeded(
                "Transaction-selection call skipped: resolution deadline reached",
                {"last_error": last_error, "attempt": attempt},
            )
        try:
            response = requests.post(
                url,
                json=payload,
                headers=cluster.get_auth_headers_for(url),
                timeout=_capped(timeout, deadline),
            )
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
            if attempt == retries:
                break
            _pause(delay * attempt)
            continue
        if response.status_code in (401, 403):
            cluster.get_auth_headers_for(url, force_refresh=True)
            last_error = f"HTTP {response.status_code}"
            _pause(delay)
            continue
        if response.status_code == 200:
            try:
                body = response.json()
            except ValueError:
                last_error = "non-JSON response"
                _pause(delay)
                continue
            if not isinstance(body, dict) or "output" not in body:
                last_error = "response has no 'output'"
                _pause(delay)
                continue
            output = body["output"]
            return output if isinstance(output, list) else []
        if response.status_code == 400 and "incorrect number of items" in response.text:
            last_error = "incorrect number of items"
            _pause(delay)
            continue
        if response.status_code in (429, 500, 502, 503, 504):
            last_error = f"HTTP {response.status_code}"
            _pause(delay * attempt)
            continue
        raise ClusterServiceError(
            "Transaction-selection service failed",
            {"status_code": response.status_code, "response": response.text[:500]},
        )
    raise ClusterServiceError(
        f"Transaction-selection service failed after {retries} attempts",
        {"error": last_error},
    )


def _run_batches(
    items: list[Any],
    build_payload: Callable[[list[Any]], dict[str, Any]],
    batch_size: int,
    workers: int,
    stage: str,
    deadline: float | None = None,
) -> tuple[list[tuple[Any, dict[str, Any]]], bool]:
    """Send ``items`` in batches (concurrently) and pair each item with its
    answer object. A batch whose answer count does not match, or that fails
    after its retries, contributes nothing (logged), as in the pipeline.
    Returns (answers, complete): ``complete`` is False when the deadline
    cut the stage short, so the caller knows the answers are partial."""
    if not items:
        return [], True
    batches = [
        items[i : i + batch_size] for i in range(0, len(items), max(1, batch_size))
    ]
    timed_out: list[bool] = []

    def one(batch: list[Any]) -> list[tuple[Any, dict[str, Any]]]:
        left = remaining(deadline)
        if left is not None and left <= 0:
            timed_out.append(True)  # skipped: nothing left to spend on it
            return []
        try:
            results = send_transaction_selection(
                build_payload(batch), deadline=deadline
            )
        except DeadlineExceeded as e:
            timed_out.append(True)
            cluster._log(
                "Transaction-selection batch abandoned at deadline",
                {"stage": stage, "items": len(batch), **e.details},
            )
            return []
        except ClusterServiceError as e:
            cluster._log(
                "Transaction-selection batch failed",
                {"stage": stage, "items": len(batch), "error": e.message, **e.details},
            )
            return []
        except Exception as e:  # noqa: BLE001 — one batch never sinks the stage
            cluster._log(
                "Transaction-selection batch failed",
                {
                    "stage": stage,
                    "items": len(batch),
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            return []
        if len(results) != len(batch):
            cluster._log(
                "Transaction-selection batch answer count mismatch",
                {"stage": stage, "items": len(batch), "answers": len(results)},
            )
            return []
        return [
            (item, r if isinstance(r, dict) else {})
            for item, r in zip(batch, results, strict=True)
        ]

    out: list[tuple[Any, dict[str, Any]]] = []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(batches)))) as pool:
        for chunk in pool.map(one, batches):
            out.extend(chunk)
    return out, not timed_out


def _yes(answer: dict[str, Any]) -> bool:
    return str(answer.get("answer", "")).strip().lower() == "yes"


# A node id in the results table is the concept id with a per-row suffix
# ("C3166217-0", "C3166217-1"); the CUI the response carries is the part
# before it, so one concept is one code whichever row selected it.
_NODE_ID_SUFFIX = re.compile(r"-\d+$")


def cui_from_node_id(node_id: Any) -> str:
    return _NODE_ID_SUFFIX.sub("", str(node_id or "").strip())


# ---------------------------------------------------------------------------
# Payloads (the pipeline's own texts, reproduced)
# ---------------------------------------------------------------------------
def _model_name() -> str:
    return str(_setting("TRANSACTION_SELECTION_MODEL", "gemini-2.5-flash"))


def generate_activity_transaction_payload(
    activities: list[dict[str, Any]],
    search_document_name: str,
    search_context: str | None = None,
) -> dict[str, Any]:
    has_context = bool(search_context and search_context.strip())
    if has_context:
        ctx = search_context.strip()
        transactions = [
            [search_document_name, ctx, a["activity_name"], a["activity_definition"]]
            for a in activities
        ]
        instructions = [
            "Each transaction contains exactly 4 elements.",
            "The first element is the search document name.",
            "The second element is the search context.",
            "The third element is the candidate activity name.",
            "The fourth element is the candidate activity definition.",
        ]
        elements = {
            "search_document_name": (
                "The original document name being searched and the target "
                "against which the candidate activity is evaluated."
            ),
            "search_context": (
                "The specific user or clinical context provided to qualify "
                "and guide the relevance evaluation."
            ),
            "activity_name": "The candidate activity name.",
            "activity_definition": "The definition of the candidate activity.",
        }
        guidelines = [
            "Determine whether the candidate activity is related to the search document name in the given search context.",
            "Consider synonyms and synonymous terminology.",
            "Consider procedural relationships between the document and activity.",
            "Consider the clinical context provided by both the document and the explicit search context.",
            "Consider parental and child classifications.",
            "Do not require an exact lexical match.",
            "A semantic equivalent or closely related activity under the provided context should be considered related.",
            "Use Activity Name first and Activity Definition to resolve ambiguity.",
        ]
    else:
        transactions = [
            [search_document_name, a["activity_name"], a["activity_definition"]]
            for a in activities
        ]
        instructions = [
            "Each transaction contains exactly 3 elements.",
            "The first element is the search document name.",
            "The second element is the candidate activity name.",
            "The third element is the candidate activity definition.",
        ]
        elements = {
            "search_document_name": (
                "The original document name being searched and the target "
                "against which the candidate activity is evaluated."
            ),
            "activity_name": "The candidate activity name.",
            "activity_definition": "The definition of the candidate activity.",
        }
        guidelines = [
            "Determine whether the candidate activity is related to the search document name.",
            "Consider synonyms and synonymous terminology.",
            "Consider procedural relationships between the document and activity.",
            "Consider the clinical context of the document and activity.",
            "Consider parental and child classifications.",
            "Do not require an exact lexical match.",
            "A semantic equivalent or closely related activity should be considered related.",
            "Use Activity Name first and Activity Definition to resolve ambiguity.",
        ]
    return {
        "model_name": _model_name(),
        "transactions": transactions,
        "objective": (
            "For each transaction, determine whether the candidate activity "
            "is related to the search document name"
            + (" within the specified search context." if has_context else ".")
            + " The purpose is to retain activities that are relevant to the searched document."
        ),
        "definitions": {"instructions": instructions, "elements": elements},
        "analysis_guidelines": guidelines,
        "output_spec": {
            "instructions": [
                "Return exactly one result for every transaction.",
                "Return results in exactly the same order as the input transactions.",
                "Return Yes when the candidate activity is related to the search document.",
                "Return No when the candidate activity is not related to the search document.",
                "Provide a short reasoning explaining the decision.",
            ],
            "response_fields": {
                "answer": {
                    "Yes": "The candidate activity is related to the search document.",
                    "No": "The candidate activity is not related to the search document.",
                },
                "reasoning": "Brief explanation of the decision.",
            },
        },
    }


def generate_document_transaction_payload(
    document_names: list[str],
    search_document_name: str,
    search_context: str | None = None,
) -> dict[str, Any]:
    has_context = bool(search_context and search_context.strip())
    if has_context:
        ctx = search_context.strip()
        transactions = [[search_document_name, ctx, d] for d in document_names]
        instructions = [
            "Each transaction contains exactly 3 elements.",
            "The first element is the search document name.",
            "The second element is the search context.",
            "The third element is the candidate document name.",
        ]
        elements = {
            "search_document_name": "The original target document name.",
            "search_context": "The specific context guiding the evaluation.",
            "candidate_document_name": "A candidate related document name.",
        }
        guidelines = [
            "Determine whether the candidate document is related to the search document within the provided context.",
            "Consider synonyms and synonymous terminology.",
            "Consider parent-child and hierarchical relationships.",
            "Consider clinically meaningful associations between concepts under the given context.",
            "Consider whether the candidate represents a procedure, finding, condition, measurement, drug, or other concept that is meaningfully related to the target and context.",
            "Do not require an exact lexical match.",
            "Do not infer a relationship merely because the terms share a word.",
            "Return Yes only when there is a reasonable semantic relationship.",
        ]
    else:
        transactions = [[search_document_name, d] for d in document_names]
        instructions = [
            "Each transaction contains exactly 2 elements.",
            "The first element is the search document name.",
            "The second element is the candidate document name.",
        ]
        elements = {
            "search_document_name": "The original target document name.",
            "candidate_document_name": "A candidate related document name.",
        }
        guidelines = [
            "Determine whether the candidate document is related to the search document.",
            "Consider synonyms and synonymous terminology.",
            "Consider parent-child and hierarchical relationships.",
            "Consider clinically meaningful associations between concepts.",
            "Consider whether the candidate represents a procedure, finding, condition, measurement, drug, or other concept that is meaningfully related to the target.",
            "Do not require an exact lexical match.",
            "Do not infer a relationship merely because the terms share a word.",
            "Return Yes only when there is a reasonable semantic relationship.",
        ]
    return {
        "model_name": _model_name(),
        "transactions": transactions,
        "objective": (
            "Determine whether each candidate document name is related to "
            "the search document name"
            + (" within the specified search context." if has_context else ".")
            + " The candidates are possible related documents discovered from activities. Do not require exact wording."
        ),
        "definitions": {"instructions": instructions, "elements": elements},
        "analysis_guidelines": guidelines,
        "output_spec": {
            "instructions": [
                "Return exactly one result for every transaction.",
                "Return results in exactly the same order as the input transactions.",
                "Return Yes if the candidate document is related to the search document.",
                "Return No if it is not related.",
                "Provide a short reasoning.",
            ],
            "response_fields": {
                "answer": {
                    "Yes": "The candidate document is related to the search document.",
                    "No": "The candidate document is not related to the search document.",
                },
                "reasoning": "Brief explanation of the relationship decision.",
            },
        },
    }


def generate_class_transaction_payload(
    classes: list[dict[str, Any]],
    search_document_name: str,
    search_context: str | None = None,
) -> dict[str, Any]:
    has_context = bool(search_context and search_context.strip())
    if has_context:
        ctx = search_context.strip()
        transactions = [
            [search_document_name, ctx, c["document_name"], c["document_class"]]
            for c in classes
        ]
        instructions = [
            "Each transaction contains exactly 4 elements.",
            "The first element is the original search document name.",
            "The second element is the search context.",
            "The third element is the candidate related document name.",
            "The fourth element is the candidate document class.",
        ]
        elements = {
            "search_document_name": "The original target document.",
            "search_context": "The specific context provided to guide the evaluation.",
            "candidate_document_name": "A candidate document discovered through previous stages.",
            "document_class": "The candidate document class to evaluate.",
        }
        guidelines = [
            "Determine whether the candidate document class is related to the search document under the given context.",
            "Use the candidate document name as supporting context for the document class.",
            "Evaluate the meaning of the document class within the provided context.",
            "Consider synonyms and synonymous terminology.",
            "Consider parent-child and hierarchical relationships.",
            "Consider clinically meaningful associations in the given context.",
            "Do not require exact lexical overlap.",
            "Do not reject a relationship solely because terminology differs.",
            "Do not mark Yes merely because the document class belongs to the same broad general domain.",
            "Return Yes only when the document class has a reasonable relationship to the search document within the context.",
        ]
    else:
        transactions = [
            [search_document_name, c["document_name"], c["document_class"]]
            for c in classes
        ]
        instructions = [
            "Each transaction contains exactly 3 elements.",
            "The first element is the original search document name.",
            "The second element is the candidate related document name.",
            "The third element is the candidate document class.",
        ]
        elements = {
            "search_document_name": "The original target document.",
            "candidate_document_name": "A candidate document discovered through previous stages.",
            "document_class": "The candidate document class to evaluate.",
        }
        guidelines = [
            "Determine whether the candidate document class is related to the search document.",
            "Use the candidate document name as supporting context for the document class.",
            "Evaluate the meaning of the document class.",
            "Consider synonyms and synonymous terminology.",
            "Consider parent-child and hierarchical relationships.",
            "Consider clinically meaningful associations.",
            "Do not require exact lexical overlap.",
            "Do not reject a relationship solely because terminology differs.",
            "Do not mark Yes merely because the document class is broadly clinical or belongs to the same very general domain.",
            "Return Yes only when the document class has a reasonable relationship to the search document.",
        ]
    return {
        "model_name": _model_name(),
        "transactions": transactions,
        "objective": (
            "Determine whether the candidate document class is related to "
            "the search document name"
            + (" within the specified search context." if has_context else ".")
            + " The search document remains the original target for every transaction."
        ),
        "definitions": {"instructions": instructions, "elements": elements},
        "analysis_guidelines": guidelines,
        "output_spec": {
            "instructions": [
                "Return exactly one result for every transaction.",
                "Return results in exactly the same order as the input transactions.",
                "Return Yes when the candidate document class is related to the search document.",
                "Return No when the candidate document class is not related.",
                "Provide a short reasoning explaining the decision.",
            ],
            "response_fields": {
                "answer": {
                    "Yes": "The candidate document class is related to the search document.",
                    "No": "The candidate document class is not related to the search document.",
                },
                "reasoning": "Brief explanation of the decision.",
            },
        },
    }


# ---------------------------------------------------------------------------
# BigQuery lookups (the script's queries)
# ---------------------------------------------------------------------------
def _query(sql: str, name: str, values: list[str]) -> list[dict[str, Any]]:
    from google.cloud import bigquery

    job_config = bigquery.QueryJobConfig(
        query_parameters=[bigquery.ArrayQueryParameter(name, "STRING", values)]
    )
    try:
        rows = cluster.get_bq_client().query(sql, job_config=job_config).result()
        return [dict(row.items()) for row in rows]
    except ClusterServiceError:
        raise
    except Exception as e:  # any BigQuery failure is a 503
        raise ClusterServiceError(
            "Transaction pipeline BigQuery lookup failed",
            {"error_type": type(e).__name__, "error": str(e)},
        ) from e


def get_activities_for_documents(document_names: Iterable[str]) -> list[dict[str, Any]]:
    """Candidate activities of the probable documents: one row per distinct
    (document_name, activity_id, activity_name, activity_definition), rows
    without an activity name dropped — as the script."""
    names = _names(document_names)
    if not names:
        return []
    table = _table(
        str(
            _setting("CLUSTER_ACTIVITY_TABLE", "SCENARIO_6_ACTIVITY_CODE_DETAILS_ARRAY")
        )
    )
    sql = f"""
        SELECT DISTINCT
            document_name,
            activity_details.activity_id AS activity_id,
            activity_details.activity_name AS activity_name,
            activity_details.activity_definition AS activity_definition
        FROM `{table}`
        CROSS JOIN UNNEST(activity_details) AS activity_details
        WHERE document_name IN UNNEST(@document_names)
        ORDER BY document_name, activity_id
    """
    out: list[dict[str, Any]] = []
    for row in _query(sql, "document_names", names):
        activity_name = str(row.get("activity_name") or "").strip()
        if not activity_name:
            continue
        out.append(
            {
                "document_name": str(row.get("document_name") or ""),
                "activity_id": str(row.get("activity_id") or "").strip(),
                "activity_name": row.get("activity_name"),
                "activity_definition": row.get("activity_definition"),
            }
        )
    return out


def get_related_document_names(activity_ids: Iterable[str]) -> list[str]:
    """Documents that carry any of the selected activities."""
    ids = [str(a).strip() for a in activity_ids if str(a).strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    table = _table(
        str(
            _setting("CLUSTER_ACTIVITY_TABLE", "SCENARIO_6_ACTIVITY_CODE_DETAILS_ARRAY")
        )
    )
    sql = f"""
        SELECT DISTINCT document_name
        FROM `{table}`
        CROSS JOIN UNNEST(activity_details) AS activity_details
        WHERE activity_details.activity_id IN UNNEST(@activity_ids)
        ORDER BY document_name
    """
    out: list[str] = []
    for row in _query(sql, "activity_ids", ids):
        name = str(row.get("document_name") or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def get_document_classes_from_results(
    document_names: Iterable[str],
) -> list[dict[str, Any]]:
    """Document classes (with their node id) of the selected documents, from
    the results table: rows of {document_name, document_class, node_id}."""
    names = _names(document_names)
    if not names:
        return []
    table = _table(str(_setting("CLUSTER_ALL_RESULTS_TABLE", "all_results")))
    doc_col = str(_setting("CLUSTER_ALL_RESULTS_DOC_COLUMN", "document_type"))
    class_col = str(_setting("CLUSTER_ALL_RESULTS_CLASS_COLUMN", "document_class"))
    code_col = str(_setting("CLUSTER_ALL_RESULTS_CODE_COLUMN", "node_id"))
    sql = f"""
        SELECT DISTINCT
            {doc_col} AS document_name,
            {class_col} AS document_class,
            {code_col} AS node_id
        FROM `{table}`
        WHERE {doc_col} IN UNNEST(@document_names)
            AND {class_col} IS NOT NULL
            AND TRIM({class_col}) != ''
        ORDER BY document_name, document_class, node_id
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in _query(sql, "document_names", names):
        item = (
            str(row.get("document_name") or "").strip(),
            str(row.get("document_class") or "").strip(),
            str(row.get("node_id") or "").strip(),
        )
        if not item[1] or item in seen:
            continue
        seen.add(item)
        out.append(
            {"document_name": item[0], "document_class": item[1], "node_id": item[2]}
        )
    return out


# ---------------------------------------------------------------------------
# The pipeline for one (search document name, context)
# ---------------------------------------------------------------------------
def resolve_via_transactions(
    search_document_name: str,
    search_context: str | None = None,
    *,
    deadline: float | None = None,
) -> list[dict[str, Any]]:
    """Run the seven steps for one record-type label. Returns
    ``[{"name": document_class, "coding": [{"code": node_id}]}, ...]`` or []
    when any stage selects nothing (logged with the stage that stopped it).

    ``deadline`` (time.monotonic()) bounds the whole run: the pipeline stops
    at the first stage it cannot start or finish in time and logs where it
    stopped. Classes the service had already confirmed by then are returned
    (they are real selections, only fewer); anything earlier yields [].
    """
    batch = int(_setting("TRANSACTION_BATCH_SIZE", 25))
    workers = int(_setting("TRANSACTION_MAX_WORKERS", 30))
    stages: dict[str, Any] = {"search": search_document_name}
    started = time.monotonic()

    def _out_of_time(stage: str) -> bool:
        left = remaining(deadline)
        if left is not None and left <= 0:
            stages["seconds"] = round(time.monotonic() - started, 1)
            _log_stages(stages, f"deadline reached before {stage}")
            return True
        return False

    # 1. cluster selection
    probable = cluster.get_probable_documents(
        search_document_name, search_context, timeout=remaining(deadline)
    )
    stages["probable_documents"] = len(probable)
    if not probable:
        _log_stages(stages, "no probable documents")
        return []

    # 2-3. activities and their selection
    if _out_of_time("activities"):
        return []
    activities = get_activities_for_documents(probable)
    stages["activities"] = len(activities)
    if not activities:
        _log_stages(stages, "no activities")
        return []
    selected, complete = _run_batches(
        activities,
        lambda b: generate_activity_transaction_payload(
            b, search_document_name, search_context
        ),
        batch,
        workers,
        "activity",
        deadline,
    )
    activity_ids = [a["activity_id"] for a, ans in selected if _yes(ans)]
    stages["activities_selected"] = len(activity_ids)
    if not complete:
        stages["seconds"] = round(time.monotonic() - started, 1)
        _log_stages(stages, "deadline reached during activity selection")
        return []
    if not activity_ids:
        _log_stages(stages, "no activity selected")
        return []

    # 4-5. related documents and their selection
    if _out_of_time("related documents"):
        return []
    related = get_related_document_names(activity_ids)
    stages["related_documents"] = len(related)
    if not related:
        _log_stages(stages, "no related documents")
        return []
    selected_docs, complete = _run_batches(
        related,
        lambda b: generate_document_transaction_payload(
            b, search_document_name, search_context
        ),
        batch,
        workers,
        "document",
        deadline,
    )
    yes_docs = [d for d, ans in selected_docs if _yes(ans)]
    stages["documents_selected"] = len(yes_docs)
    if not complete:
        stages["seconds"] = round(time.monotonic() - started, 1)
        _log_stages(stages, "deadline reached during document selection")
        return []
    if not yes_docs:
        _log_stages(stages, "no document selected")
        return []

    # 6-7. document classes and their selection
    if _out_of_time("document classes"):
        return []
    classes = get_document_classes_from_results(yes_docs)
    stages["classes"] = len(classes)
    if not classes:
        _log_stages(stages, "no document classes")
        return []
    selected_classes, complete = _run_batches(
        classes,
        lambda b: generate_class_transaction_payload(
            b, search_document_name, search_context
        ),
        batch,
        workers,
        "class",
        deadline,
    )
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for c, ans in selected_classes:
        if not _yes(ans):
            continue
        code = cui_from_node_id(c["node_id"])
        if not code or code in seen:
            continue
        seen.add(code)
        hits.append({"name": c["document_class"], "coding": [{"code": code}]})
    stages["classes_selected"] = len(hits)
    stages["seconds"] = round(time.monotonic() - started, 1)
    if not complete:
        # Partial: the classes confirmed before the deadline are kept — they
        # are the service's own selections, just not all of them.
        _log_stages(
            stages,
            "ok (partial: deadline reached during class selection)"
            if hits
            else "deadline reached during class selection",
        )
        return hits
    _log_stages(stages, "ok" if hits else "no class selected")
    return hits


def _log_stages(stages: dict[str, Any], outcome: str) -> None:
    cluster._log(
        "Record-type transaction pipeline",
        {**stages, "outcome": outcome},
        severity="INFO" if outcome == "ok" else "WARNING",
    )
