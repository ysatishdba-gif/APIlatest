"""Record-type resolution through the transaction-selection pipeline
(app/utils/document_transaction_pipeline.py): the standalone link-code flow
made callable per record type — cluster selection -> activities -> Stage 1
-> related documents -> Stage 2 -> document classes -> Stage 3 -> CUIs."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app import config
from app.utils import context_lonic_document_cluster as dc
from app.utils import document_transaction_pipeline as tx

URL = "https://transactions.example/select"


@pytest.fixture
def pipeline_on(monkeypatch):
    monkeypatch.setattr(config, "TRANSACTION_SELECTION_URL", URL)
    monkeypatch.setattr(config, "RECORD_TYPE_RESOLUTION_MODE", "auto")
    monkeypatch.setattr(
        config, "CLUSTER_SELECTION_URL", "https://cluster.example/select"
    )
    monkeypatch.setattr(config, "CLUSTER_BQ_DATASET", "ds")
    monkeypatch.setattr(config, "TRANSACTION_BATCH_SIZE", 2)
    monkeypatch.setattr(config, "TRANSACTION_RETRY_DELAY_SECONDS", 0)
    yield


class _FakeTransactionService:
    """Answers Yes when the candidate (last element of a transaction) contains
    ``yes_marker``; records every payload; can fail the first call."""

    def __init__(self, yes_marker: str = "REL", fail_first: str | None = None):
        self.yes_marker = yes_marker
        self.payloads: list[dict] = []
        self.fail_first = fail_first

    def post(self, url, json=None, headers=None, timeout=None):
        assert url == URL
        self.payloads.append(json)
        resp = MagicMock()
        if self.fail_first:
            kind, self.fail_first = self.fail_first, None
            if kind == "count":
                resp.status_code = 400
                resp.text = "incorrect number of items"
                return resp
            if kind == "503":
                resp.status_code = 503
                resp.text = "unavailable"
                return resp
        resp.status_code = 200
        out = []
        for t in json["transactions"]:
            candidate = " ".join(str(x) for x in t[1:])
            out.append(
                {
                    "answer": "Yes" if self.yes_marker in candidate else "No",
                    "reasoning": "r",
                }
            )
        resp.json.return_value = {"output": out}
        resp.text = ""
        return resp


def _fake_bq(sql: str, name: str, values: list[str]) -> list[dict]:
    """Three tables, keyed by which SQL ran."""
    if "activity_details.activity_id AS activity_id" in sql:  # activities of documents
        rows = []
        for doc in values:
            if doc == "lab report":
                rows += [
                    {
                        "document_name": "Lab Report",
                        "activity_id": "A1",
                        "activity_name": "REL glucose test",
                        "activity_definition": "d1",
                    },
                    {
                        "document_name": "Lab Report",
                        "activity_id": "A2",
                        "activity_name": "billing",
                        "activity_definition": "d2",
                    },
                    {
                        "document_name": "Lab Report",
                        "activity_id": "A3",
                        "activity_name": "REL hba1c test",
                        "activity_definition": "d3",
                    },
                ]
        return rows
    if "SELECT DISTINCT document_name" in sql:  # documents of activities
        docs = {
            "A1": ["Lab Report", "REL Chemistry Panel"],
            "A3": ["REL HbA1c Result"],
            "A2": ["Invoice"],
        }
        return [{"document_name": d} for a in values for d in docs.get(a, [])]
    if "AS node_id" in sql:  # classes of documents
        # node ids as the results table carries them: concept id + row suffix
        classes = {
            "lab report": [("Laboratory", "C_LAB-0")],
            "rel chemistry panel": [
                ("REL Laboratory", "C_LAB-0"),
                ("Chemistry", "C_CHEM-2"),
            ],
            "rel hba1c result": [
                ("REL Laboratory", "C_LAB-1"),
                ("Diabetes REL Monitoring", "C_DM-0"),
            ],
        }
        return [
            {"document_name": d, "document_class": c, "node_id": n}
            for d in values
            for c, n in classes.get(d, [])
        ]
    raise AssertionError(sql)


class TestTransactionPipeline:
    def test_disabled_without_url_or_in_cluster_mode(self, monkeypatch):
        monkeypatch.setattr(config, "TRANSACTION_SELECTION_URL", "")
        assert not tx.is_enabled()
        monkeypatch.setattr(config, "TRANSACTION_SELECTION_URL", URL)
        assert tx.is_enabled()
        monkeypatch.setattr(config, "RECORD_TYPE_RESOLUTION_MODE", "cluster")
        assert not tx.is_enabled()

    def test_node_id_suffix_is_stripped_to_the_cui(self):
        assert tx.cui_from_node_id("C3166217-0") == "C3166217"
        assert tx.cui_from_node_id(" C3166217-12 ") == "C3166217"
        assert tx.cui_from_node_id("C3166217") == "C3166217"
        assert tx.cui_from_node_id(None) == ""

    def test_payload_shapes_with_and_without_context(self):
        acts = [{"activity_name": "a", "activity_definition": "d"}]
        p = tx.generate_activity_transaction_payload(acts, "Lab Report", "diabetes")
        assert p["transactions"] == [["Lab Report", "diabetes", "a", "d"]]
        assert (
            p["definitions"]["instructions"][0]
            == "Each transaction contains exactly 4 elements."
        )
        p = tx.generate_activity_transaction_payload(acts, "Lab Report", None)
        assert p["transactions"] == [["Lab Report", "a", "d"]]
        p = tx.generate_document_transaction_payload(["X"], "Lab Report", "")
        assert p["transactions"] == [["Lab Report", "X"]]
        p = tx.generate_class_transaction_payload(
            [{"document_name": "X", "document_class": "K"}], "Lab Report", "ctx"
        )
        assert p["transactions"] == [["Lab Report", "ctx", "X", "K"]]
        assert set(p) == {
            "model_name",
            "transactions",
            "objective",
            "definitions",
            "analysis_guidelines",
            "output_spec",
        }

    def test_seven_steps_select_classes_and_return_node_ids(self, pipeline_on):
        service = _FakeTransactionService()
        with (
            patch.object(dc, "get_probable_documents", return_value=["LAB REPORT"]),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "_log"),
        ):
            hits = tx.resolve_via_transactions("Lab Report", "diabetes")
        # Stage 1 kept A1 and A3 (REL), dropped billing; Stage 2 kept the REL
        # documents; Stage 3 kept the REL classes -> distinct CUIs (the node
        # ids C_LAB-0 and C_LAB-1 are one concept, one code)
        assert hits == [
            {"name": "REL Laboratory", "coding": [{"code": "C_LAB"}]},
            {"name": "Diabetes REL Monitoring", "coding": [{"code": "C_DM"}]},
        ]
        stages = [p["definitions"]["instructions"][0] for p in service.payloads]
        # batch size 2: activities 3 -> 2 batches (4 elements); related documents
        # 3 -> 2 batches (3 elements); classes 4 -> 2 batches (4 elements)
        assert stages.count("Each transaction contains exactly 4 elements.") == 4
        assert stages.count("Each transaction contains exactly 3 elements.") == 2
        assert all(
            p["model_name"] == config.TRANSACTION_SELECTION_MODEL
            for p in service.payloads
        )

    def test_count_mismatch_and_transient_errors_are_retried(self, pipeline_on):
        service = _FakeTransactionService(fail_first="count")
        with (
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
        ):
            out = tx.send_transaction_selection(
                tx.generate_document_transaction_payload(["REL x"], "s")
            )
        assert (
            out == [{"answer": "Yes", "reasoning": "r"}] and len(service.payloads) == 2
        )
        service = _FakeTransactionService(fail_first="503")
        with (
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
        ):
            assert tx.send_transaction_selection(
                tx.generate_document_transaction_payload(["x"], "s")
            ) == [{"answer": "No", "reasoning": "r"}]

    def test_stage_with_nothing_selected_yields_no_hits(self, pipeline_on):
        service = _FakeTransactionService(yes_marker="NEVER")
        with (
            patch.object(dc, "get_probable_documents", return_value=["Lab Report"]),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "_log") as log,
        ):
            assert tx.resolve_via_transactions("Lab Report", "diabetes") == []
        assert log.call_args.args[1]["outcome"] == "no activity selected"

    def test_bigquery_comparisons_are_normalised(self, pipeline_on):
        captured = {}

        def q(sql, name, values):
            captured[name] = (sql, values)
            return []

        with patch.object(tx, "_query", side_effect=q):
            tx.get_activities_for_documents([" Lab Report", "LAB REPORT"])
            tx.get_document_classes_from_results(["Chest X-Ray "])
        assert captured["document_names"][1] == ["chest x-ray"]
        assert "LOWER(TRIM(" in captured["document_names"][0]

    def test_record_types_use_the_pipeline_first_then_fallbacks(self, pipeline_on):
        service = _FakeTransactionService()
        cat = dc.DocumentCatalog(
            [
                {
                    "document_class_cui": "C_NOTE",
                    "document_class": "Note",
                    "possible_document_name": "Progress Note",
                }
            ]
        )
        with (
            patch.object(
                dc,
                "get_probable_documents",
                side_effect=lambda n, c, timeout=None: (
                    ["Lab Report"] if "lab" in n.lower() else []
                ),
            ),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "get_document_catalog", return_value=cat),
            patch.object(dc, "_log"),
        ):
            dc._catalog_state["catalog"] = cat
            out = dc.resolve_record_type_cuis(
                ["Laboratory Report", "Progress Note"],
                tags=["diabetes"],
                aliases={"laboratory report": ["Lab Report"]},
            )
        # Laboratory Report: its own label found no probable documents, its
        # alias "Lab Report" ran the pipeline -> node ids. Progress Note: the
        # pipeline found nothing -> the catalog stages coded it.
        assert out == [
            {
                "name": "Laboratory Report",
                "coding": [{"code": "C_LAB"}, {"code": "C_DM"}],
            },
            {"name": "Progress Note", "coding": [{"code": "C_NOTE"}]},
        ]

    def test_pipeline_failure_falls_through_to_other_stages(self, pipeline_on):
        cat = dc.DocumentCatalog(
            [
                {
                    "document_class_cui": "C_LAB",
                    "document_class": "Laboratory",
                    "possible_document_name": "Lab Report",
                }
            ]
        )
        with (
            patch.object(dc, "get_probable_documents", return_value=["Lab Report"]),
            patch.object(tx, "_query", side_effect=RuntimeError("bq down")),
            patch.object(dc, "get_document_catalog", return_value=cat),
            patch.object(dc, "_log") as log,
        ):
            dc._catalog_state["catalog"] = cat
            out = dc.resolve_record_type_cuis(["Lab Report"], tags=[])
        assert out == [{"name": "Lab Report", "coding": [{"code": "C_LAB"}]}]
        assert any(
            c.args[0] == "Record-type transaction pipeline failed"
            for c in log.call_args_list
        )

    def test_transaction_service_gets_its_own_audience_token(self, monkeypatch):
        monkeypatch.setattr(
            config, "CLUSTER_SELECTION_URL", "https://cluster.example/select"
        )
        dc.reset_auth_cache()
        with patch.object(
            dc,
            "_fetch_identity_token",
            side_effect=lambda aud=None: (
                f"tok-for-{aud or config.CLUSTER_SELECTION_URL}"
            ),
        ) as fetch:
            h1 = dc.get_auth_headers_for(URL)
            h2 = dc.get_auth_headers_for(URL)
            h3 = dc.get_auth_headers()
        assert h1["Authorization"] == f"Bearer tok-for-{URL}" and h1 == h2
        assert h3["Authorization"] == "Bearer tok-for-https://cluster.example/select"
        assert fetch.call_count == 2  # one per audience, cached
        dc.reset_auth_cache()


class TestResolutionDeadline:
    """The pipeline runs inside a request: it works against a deadline and
    never lets a dependency failure reach the caller as an exception."""

    def test_call_timeout_is_capped_by_the_deadline(self, pipeline_on):
        import time

        seen: list[float] = []

        def post(url, json=None, headers=None, timeout=None):
            seen.append(timeout)
            resp = MagicMock()
            resp.status_code = 200
            resp.text = ""
            resp.json.return_value = {"output": [{"answer": "No"}]}
            return resp

        payload = tx.generate_document_transaction_payload(["x"], "s")
        with (
            patch.object(tx.requests, "post", side_effect=post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(config, "TRANSACTION_REQUEST_TIMEOUT_SECONDS", 60),
        ):
            tx.send_transaction_selection(payload, deadline=time.monotonic() + 5)
            tx.send_transaction_selection(payload, deadline=None)
            tx.send_transaction_selection(payload, deadline=time.monotonic() + 0.01)
        assert 4 < seen[0] <= 5 and seen[1] == 60 and seen[2] == 1.0

    def test_retry_never_sleeps_past_the_deadline(self, pipeline_on, monkeypatch):
        import time

        monkeypatch.setattr(config, "TRANSACTION_RETRY_DELAY_SECONDS", 3)
        service = _FakeTransactionService(fail_first="503")
        slept: list[float] = []
        with (
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(tx.time, "sleep", side_effect=slept.append),
            pytest.raises(tx.DeadlineExceeded) as err,
        ):
            tx.send_transaction_selection(
                tx.generate_document_transaction_payload(["x"], "s"),
                deadline=time.monotonic() + 2,  # a 3 s back-off would overrun
            )
        assert slept == [] and len(service.payloads) == 1
        assert err.value.details["last_error"] == "HTTP 503"
        # with time to spare the same failure is retried (back-off 3 s)
        service = _FakeTransactionService(fail_first="503")
        with (
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(tx.time, "sleep", side_effect=slept.append),
        ):
            tx.send_transaction_selection(
                tx.generate_document_transaction_payload(["x"], "s"),
                deadline=time.monotonic() + 60,
            )
        assert slept == [3] and len(service.payloads) == 2

    def test_expired_deadline_stops_the_pipeline_between_stages(self, pipeline_on):
        import time

        service = _FakeTransactionService()
        with (
            patch.object(dc, "get_probable_documents", return_value=["Lab Report"]),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "_log") as log,
        ):
            out = tx.resolve_via_transactions(
                "Lab Report", "diabetes", deadline=time.monotonic() - 1
            )
        assert out == [] and service.payloads == []
        assert log.call_args.args[1]["outcome"] == "deadline reached before activities"

    def test_classes_confirmed_before_the_deadline_are_kept(self, pipeline_on):
        """Deadline during Stage 3: the classes already answered Yes are the
        service's own selections and are returned (partial), logged as such."""
        import time

        service = _FakeTransactionService()
        deadline = {"at": None}
        calls = {"n": 0}

        def post(url, json=None, headers=None, timeout=None):
            calls["n"] += 1
            if "document_class" in json["definitions"]["elements"]:
                # Stage 3: the first batch answers, then the budget is gone
                if calls.get("stage3_seen"):
                    raise tx.requests.Timeout("hung")
                calls["stage3_seen"] = True
            return service.post(url, json=json, headers=headers, timeout=timeout)

        with (
            patch.object(dc, "get_probable_documents", return_value=["Lab Report"]),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(config, "TRANSACTION_MAX_WORKERS", 1),
            patch.object(config, "TRANSACTION_RETRY_DELAY_SECONDS", 5),
            patch.object(dc, "_log") as log,
        ):
            deadline["at"] = time.monotonic() + 3
            out = tx.resolve_via_transactions(
                "Lab Report", "diabetes", deadline=deadline["at"]
            )
        # batch size 2, workers 1: the first class batch (REL Laboratory,
        # Chemistry) answered; the second could not be retried in time
        assert out == [{"name": "REL Laboratory", "coding": [{"code": "C_LAB"}]}]
        outcome = log.call_args.args[1]["outcome"]
        assert outcome.startswith("ok (partial")
        assert log.call_args.args[1]["classes_selected"] == 1

    def test_resolver_survives_service_and_bigquery_failures(self, pipeline_on):
        """Cluster selection raising and the document-class lookup raising are
        logged, and the catalog's class-label stage still codes the name."""
        cat = dc.DocumentCatalog(
            [
                {
                    "document_class_cui": "C_LAB",
                    "document_class": "Laboratory Report",
                    "possible_document_name": "Lab Report",
                }
            ]
        )
        with (
            patch.object(
                dc,
                "get_probable_documents",
                side_effect=dc.ClusterServiceError("cluster 500"),
            ),
            patch.object(
                dc, "get_document_classes", side_effect=RuntimeError("bq quota")
            ),
            patch.object(dc, "_log") as log,
        ):
            dc._catalog_state["catalog"] = cat
            out = dc.resolve_record_type_cuis(["Laboratory Report"], tags=["ctx"])
        assert out == [{"name": "Laboratory Report", "coding": [{"code": "C_LAB"}]}]
        messages = [c.args[0] for c in log.call_args_list]
        assert (
            "Cluster selection failed; record type continues without members"
            in messages
        )
        assert (
            "Document-class lookup failed; record types continue without members"
            in messages
        )

    def test_cluster_selection_is_called_once_per_text(self, pipeline_on):
        """The pipeline and the fan-out share one memoised cluster lookup:
        a text the pipeline already asked for is never sent again."""
        service = _FakeTransactionService(yes_marker="NEVER")
        asked: list[str] = []

        def probable(text, context, timeout=None):
            asked.append(text)
            return ["Lab Report"]

        with (
            patch.object(dc, "get_probable_documents", side_effect=probable),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "get_document_classes", return_value=[]),
            patch.object(dc, "_log"),
        ):
            dc.resolve_record_type_cuis(
                ["Lab Report"], tags=[], aliases={"lab report": ["Laboratory Report"]}
            )
        assert asked == ["Lab Report", "Laboratory Report"]
