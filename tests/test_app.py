import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import data
from main import app

client = TestClient(app)


class DataTests(unittest.TestCase):
    def test_journey_state(self):
        self.assertEqual(data.get_journey_state("PK-DEMO-1"), "pre_surgery")
        self.assertEqual(data.get_journey_state("PK-DEMO-2"), "post_surgery")
        self.assertIsNone(data.get_journey_state("PK-UNKNOWN"))

    def test_common_questions(self):
        for state in ("pre_surgery", "post_surgery"):
            questions = data.get_common_questions(state)
            self.assertEqual(len(questions), 5)
            self.assertEqual(len(set(questions)), 5)
        self.assertEqual(data.get_common_questions("other"), [])

    def test_answer_matches_known_question(self):
        for state in ("pre_surgery", "post_surgery"):
            for question in data.get_common_questions(state):
                self.assertNotEqual(data.get_answer(question, state), data.FALLBACK_ANSWER)

    def test_answer_differs_by_state(self):
        question = data.get_common_questions("post_surgery")[0]
        self.assertNotEqual(
            data.get_answer(question, "post_surgery"), data.get_answer(question, "pre_surgery")
        )

    def test_answer_fallback(self):
        self.assertEqual(data.get_answer("zzz qqq", "pre_surgery"), data.FALLBACK_ANSWER)


class ApiTests(unittest.TestCase):
    def test_health(self):
        response = client.get("/pre-surgery/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chat_page(self):
        for path in ("/pre-surgery", "/pre-surgery/"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/html", response.headers["content-type"])

    def test_patients(self):
        keys = [p["key"] for p in client.get("/pre-surgery/api/patients").json()["patients"]]
        self.assertEqual(keys, ["PK-DEMO-1", "PK-DEMO-2"])

    def test_questions(self):
        body = client.get("/pre-surgery/api/questions", params={"patient_key": "PK-DEMO-2"}).json()
        self.assertEqual(body["journey_state"], "post_surgery")
        self.assertEqual(len(body["questions"]), 5)

    def test_chat_pre_and_post(self):
        for key, state in (("PK-DEMO-1", "pre_surgery"), ("PK-DEMO-2", "post_surgery")):
            question = data.get_common_questions(state)[0]
            response = client.post(
                "/pre-surgery/api/chat", json={"patient_key": key, "message": question}
            )
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["journey_state"], state)
            self.assertEqual(body["answer"], data.get_answer(question, state))

    def test_chat_unknown_patient(self):
        response = client.post(
            "/pre-surgery/api/chat", json={"patient_key": "PK-NOPE", "message": "hello there"}
        )
        self.assertEqual(response.status_code, 404)

    def test_chat_rejects_empty_message(self):
        response = client.post(
            "/pre-surgery/api/chat", json={"patient_key": "PK-DEMO-1", "message": ""}
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
