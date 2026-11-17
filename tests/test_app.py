import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import data
from main import NO_MATCH, app

client = TestClient(app)
API = "/pre-surgery/api"


class DataTests(unittest.TestCase):
    def test_bank_size_and_ids(self):
        questions = data.list_questions()
        self.assertEqual(len(questions), 45)
        self.assertEqual(len({q["id"] for q in questions}), 45)
        self.assertEqual(len(data.list_categories()), 12)

    def test_handling_takes_most_restrictive_cluster(self):
        expected = {
            "Q01": "ai",              # A
            "Q07": "ai_with_review",  # A or B
            "Q29": "ai_with_review",  # A, with B where it depends on surgeon or procedure
            "Q13": "handoff",         # B or C
            "Q41": "handoff",         # C
            "Q43": "handoff",         # C or routing
            "Q35": "routing",
            "Q11": "needs_review",    # no cluster
            "Q03": "out_of_scope",    # Outcome 3
            "Q32": "out_of_scope",    # Meds-to-Beds
        }
        for question_id, handling in expected.items():
            self.assertEqual(data.get_question(question_id)["handling"], handling, question_id)

    def test_handoff_questions_never_show_an_answer(self):
        for question in data.list_questions():
            if question["handling"] in ("handoff", "routing", "needs_review", "out_of_scope"):
                self.assertNotIn("Answer pending", question["response"])

    def test_trigger_questions_capped_at_five(self):
        for trigger in data.list_triggers():
            questions = data.questions_for_trigger(trigger["id"])
            self.assertLessEqual(len(questions), 5)
            self.assertEqual(len(questions), min(5, trigger["question_count"]))
        self.assertIsNone(data.questions_for_trigger("nope"))

    def test_gather_flows_link_to_real_flows(self):
        self.assertEqual(len(data.list_gather_flows()), 12)
        for question in data.list_questions():
            if question["gather_flow"]:
                self.assertIsNotNone(data.get_gather_flow(question["gather_flow"]))

    def test_gather_summary(self):
        flow = data.get_gather_flow("G07")
        summary = data.gather_summary("G07", ["Two days ago"])
        self.assertEqual(summary["outcome"], "handoff")
        self.assertEqual(len(summary["gathered"]), len(flow["follow_ups"]))
        self.assertEqual(summary["gathered"][0]["answer"], "Two days ago")
        self.assertEqual(summary["gathered"][1]["answer"], "Not answered")
        self.assertIsNone(data.gather_summary("nope", []))

    def test_match(self):
        self.assertEqual(data.match("my dressing fell off")[1]["id"], "G07")
        self.assertEqual(data.match("I'm having pain")[0], "flow")
        kind, item = data.match("when can I drive again")
        self.assertEqual((kind, item["id"]), ("question", "Q29"))
        # A bank question with a gather flow starts the flow.
        self.assertEqual(data.match("is this a normal reaction to my injection")[1]["id"], "G03")
        self.assertEqual(data.match("banana"), (None, None))
        self.assertEqual(data.match("   "), (None, None))


class ApiTests(unittest.TestCase):
    def test_health_and_page(self):
        self.assertEqual(client.get(f"{API}/health").json(), {"status": "ok"})
        for path in ("/pre-surgery", "/pre-surgery/"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])

    def test_anticipate(self):
        triggers = client.get(f"{API}/triggers").json()["triggers"]
        self.assertEqual(len(triggers), 7)
        questions = client.get(f"{API}/triggers/surgery/questions").json()["questions"]
        self.assertEqual(len(questions), 5)
        self.assertEqual(client.get(f"{API}/triggers/nope/questions").status_code, 404)

    def test_answer(self):
        body = client.get(f"{API}/questions").json()
        self.assertEqual(len(body["questions"]), 45)
        filtered = client.get(f"{API}/questions", params={"category": "Injections"}).json()
        self.assertEqual(len(filtered["questions"]), 3)
        self.assertEqual(client.get(f"{API}/questions/Q16").json()["handling"], "handoff")
        self.assertEqual(client.get(f"{API}/questions/Q99").status_code, 404)

    def test_gather(self):
        self.assertEqual(len(client.get(f"{API}/gather").json()["flows"]), 12)
        response = client.post(f"{API}/gather/G04/summary", json={"answers": ["a", "b", "c"]})
        self.assertEqual(response.json()["outcome"], "handoff")
        self.assertEqual(client.post(f"{API}/gather/G99/summary", json={"answers": []}).status_code, 404)

    def test_ask(self):
        body = client.post(f"{API}/ask", json={"message": "how long is surgery"}).json()
        self.assertEqual((body["match"], body["question"]["id"]), ("question", "Q02"))
        body = client.post(f"{API}/ask", json={"message": "my medication isn't working"}).json()
        self.assertEqual((body["match"], body["flow"]["id"]), ("flow", "G08"))
        body = client.post(f"{API}/ask", json={"message": "banana"}).json()
        self.assertEqual((body["match"], body["message"]), (None, NO_MATCH))
        self.assertEqual(client.post(f"{API}/ask", json={"message": ""}).status_code, 422)

    def test_chat_answers_a_question(self):
        body = client.post(f"{API}/chat", json={"message": "do I need a brace"}).json()
        self.assertEqual(body["kind"], "ai")
        self.assertIsNone(body["state"])

    def test_chat_hands_off_cluster_c(self):
        body = client.post(f"{API}/chat", json={"message": "why am I not a surgical candidate"}).json()
        self.assertEqual(body["kind"], "handoff")

    def test_chat_runs_a_gather_flow_to_handoff(self):
        flow = data.get_gather_flow("G07")
        body = client.post(f"{API}/chat", json={"message": "my dressing fell off"}).json()
        self.assertEqual(body["kind"], "follow_up")
        self.assertIn(flow["follow_ups"][0], body["reply"])
        for index in range(len(flow["follow_ups"])):
            body = client.post(f"{API}/chat", json={"message": f"answer {index}", "state": body["state"]}).json()
            if index + 1 < len(flow["follow_ups"]):
                self.assertEqual(body["reply"], flow["follow_ups"][index + 1])
        self.assertEqual(body["kind"], "handoff")
        self.assertIsNone(body["state"])
        self.assertEqual([g["answer"] for g in body["summary"]["gathered"]],
                         [f"answer {i}" for i in range(len(flow["follow_ups"]))])

    def test_chat_no_match_and_bad_state(self):
        self.assertEqual(client.post(f"{API}/chat", json={"message": "banana"}).json()["kind"], "no_match")
        response = client.post(f"{API}/chat", json={"message": "x", "state": {"flow_id": "G99", "answers": []}})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
