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
            if doc == "Lab Report":
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
            "Lab Report": [("Laboratory", "C_LAB-0")],
            "REL Chemistry Panel": [
                ("REL Laboratory", "C_LAB-0"),
                ("Chemistry", "C_CHEM-2"),
            ],
            "REL HbA1c Result": [
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
    def test_enabled_by_the_transaction_url(self, monkeypatch):
        monkeypatch.setattr(config, "TRANSACTION_SELECTION_URL", "")
        assert not tx.is_enabled()
        monkeypatch.setattr(config, "TRANSACTION_SELECTION_URL", URL)
        assert tx.is_enabled()

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
            patch.object(dc, "get_probable_documents", return_value=["Lab Report"]),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "_log"),
        ):
            hits = tx.resolve_via_transactions("Lab Report", "diabetes")
        # Stage 1 kept A1 and A3 (REL), dropped billing; Stage 2 kept the REL
        # documents; Stage 3 kept every class of a REL document or with a REL
        # label -> distinct CUIs (node ids C_LAB-0 and C_LAB-1 are one
        # concept, one code)
        assert hits == [
            {"name": "REL Laboratory", "coding": [{"code": "C_LAB"}]},
            {"name": "Chemistry", "coding": [{"code": "C_CHEM"}]},
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

    def test_bigquery_queries_match_names_exactly_as_the_script(self, pipeline_on):
        captured = {}

        def q(sql, name, values):
            captured.setdefault(name, []).append((sql, values))
            return []

        with patch.object(tx, "_query", side_effect=q):
            tx.get_activities_for_documents([" Lab Report", "Lab Report", "LAB REPORT"])
            tx.get_document_classes_from_results(["Chest X-Ray "])
        (act_sql, act_values), (cls_sql, cls_values) = captured["document_names"]
        assert act_values == ["Lab Report", "LAB REPORT"]  # as given, distinct
        assert cls_values == ["Chest X-Ray"]
        assert "WHERE document_name IN UNNEST(@document_names)" in act_sql
        assert "WHERE document_type IN UNNEST(@document_names)" in cls_sql
        assert "LOWER(" not in act_sql + cls_sql

    def test_record_types_are_coded_by_the_pipeline_only(self, pipeline_on):
        """Each record type runs the pipeline with its own name and the tag
        context; one the pipeline finds nothing for stays uncoded."""
        service = _FakeTransactionService()
        asked = []

        def probable(name, context, timeout=None):
            asked.append((name, context))
            return ["Lab Report"] if name == "Lab Report" else []

        with (
            patch.object(dc, "get_probable_documents", side_effect=probable),
            patch.object(tx, "_query", side_effect=_fake_bq),
            patch.object(tx.requests, "post", side_effect=service.post),
            patch.object(dc, "get_auth_headers_for", return_value={}),
            patch.object(dc, "_log"),
        ):
            out = dc.resolve_record_type_cuis(
                ["Lab Report", "Progress Note"], tags=["diabetes"]
            )
        assert sorted(asked) == [
            ("Lab Report", "diabetes"),
            ("Progress Note", "diabetes"),
        ]
        assert out == [
            {
                "name": "Lab Report",
                "coding": [{"code": "C_LAB"}, {"code": "C_CHEM"}, {"code": "C_DM"}],
            },
            {"name": "Progress Note", "coding": []},
        ]

    def test_pipeline_failure_leaves_the_record_type_uncoded(self, pipeline_on):
        """A failing cluster-selection call or BigQuery query is logged and the
        record type has no codes; the other record types are unaffected."""

        def probable(name, context, timeout=None):
            if name == "Broken Cluster":
                raise dc.ClusterServiceError("cluster 500")
            return ["Lab Report"]

        def bq(sql, name, values):
            raise dc.ClusterServiceError("bq quota")

        with (
            patch.object(dc, "get_probable_documents", side_effect=probable),
            patch.object(tx, "_query", side_effect=bq),
            patch.object(dc, "_log") as log,
        ):
            out = dc.resolve_record_type_cuis(["Broken Cluster", "Lab Report"], tags=[])
        assert out == [
            {"name": "Broken Cluster", "coding": []},
            {"name": "Lab Report", "coding": []},
        ]
        failed = [
            c.args[1]["record_type"]
            for c in log.call_args_list
            if c.args[0].startswith("Record-type transaction pipeline failed")
        ]
        assert sorted(failed) == ["Broken Cluster", "Lab Report"]

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
        assert out == [
            {"name": "REL Laboratory", "coding": [{"code": "C_LAB"}]},
            {"name": "Chemistry", "coding": [{"code": "C_CHEM"}]},
        ]
        outcome = log.call_args.args[1]["outcome"]
        assert outcome.startswith("ok (partial")
        assert log.call_args.args[1]["classes_selected"] == 2
