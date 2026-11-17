"""Data layer for the Outcome 4 experiment front end.

Reads two seed files built from the experiment proposal (Appendix A and
Experiment 3). No patient data. The experiments fill in intent, acuity,
frequency, answer and answer_rating in question_bank.json; nothing else
in the app needs to change when they do.
"""
import json
import re
from pathlib import Path

_DIR = Path(__file__).parent
_BANK = json.loads((_DIR / "question_bank.json").read_text(encoding="utf-8"))
_FLOWS = json.loads((_DIR / "gather_flows.json").read_text(encoding="utf-8"))["flows"]

_QUESTIONS = {q["id"]: q for q in _BANK["questions"]}
_FLOW_BY_ID = {f["id"]: f for f in _FLOWS}
_TRIGGERS = {t["id"]: t for t in _BANK["triggers"]}

# How a question is handled, from its scope and cluster. Mixed clusters
# take the most restrictive reading.
HANDLING = {
    "ai": "Cluster A: safe for AI to answer",
    "ai_with_review": "Cluster B: AI draft plus a person",
    "handoff": "Cluster C: person only",
    "routing": "Status or routing question",
    "needs_review": "Cluster not assigned yet",
    "out_of_scope": "Outside Outcome 4",
}

_SCOPE_NOTES = {
    "outcome_3": "This is a scheduling question (Outcome 3). It is counted for volume only.",
    "outcome_2": "This is a coverage and cost question (Outcome 2). It is counted for volume only.",
    "deferred": "This caregiver question is deliberately deferred.",
    "out_of_scope": "This relates to Meds-to-Beds, which is out of scope for this code-a-thon.",
}

_STOP = {
    "the", "and", "for", "you", "your", "are", "was", "what", "when", "why", "how", "does",
    "will", "can", "should", "have", "has", "had", "this", "that", "with", "from", "about",
    "need", "get", "got", "having", "been", "not", "but", "they", "them", "there", "after",
    "before", "into", "just", "any", "isn", "don", "doesn", "haven", "its", "much", "did",
}


def _handling(question):
    if question["scope"] != "outcome_4":
        return "out_of_scope"
    cluster = question["cluster"] or ""
    letters = set(re.findall(r"\b[ABC]\b", cluster))
    if "C" in letters:
        return "handoff"
    if "routing" in cluster.lower():
        return "routing"
    if "B" in letters:
        return "ai_with_review"
    if "A" in letters:
        return "ai"
    return "needs_review"


def _response(question, handling):
    if handling == "out_of_scope":
        return _SCOPE_NOTES.get(question["scope"], "This question is outside Outcome 4.")
    if handling == "handoff":
        return "This question is for the care team. It would be handed off to a clinician, with no AI answer."
    if handling == "routing":
        return "This is a status or routing question. It would be routed to the care team."
    if handling == "needs_review":
        return "No cluster is assigned yet, so this is treated as needing clinician review."
    if question["answer"]:
        return question["answer"]
    return "Answer pending. It will come from in-basket data in Experiment 2."


def present(question):
    """Return a question with its handling, label and response text added."""
    handling = _handling(question)
    return {
        **question,
        "trigger_names": [_TRIGGERS[t]["name"] for t in question["triggers"]],
        "handling": handling,
        "handling_label": HANDLING[handling],
        "response": _response(question, handling),
    }


def list_triggers():
    """Return the care triggers with how many questions map to each."""
    return [
        {**t, "question_count": sum(t["id"] in q["triggers"] for q in _BANK["questions"])}
        for t in _BANK["triggers"]
    ]


def questions_for_trigger(trigger_id, limit=5):
    """Return up to `limit` questions for a trigger, or None if the trigger is unknown.

    Ranked by frequency once the data provides it; until then, bank order.
    """
    if trigger_id not in _TRIGGERS:
        return None
    matched = [q for q in _BANK["questions"] if trigger_id in q["triggers"]]
    matched.sort(key=lambda q: -(q["frequency"] or 0))
    return [present(q) for q in matched[:limit]]


def list_questions(category=None):
    """Return every question, optionally filtered by category."""
    return [present(q) for q in _BANK["questions"] if not category or q["category"] == category]


def list_categories():
    seen = []
    for q in _BANK["questions"]:
        if q["category"] not in seen:
            seen.append(q["category"])
    return seen


def get_question(question_id):
    question = _QUESTIONS.get(question_id)
    return present(question) if question else None


def list_gather_flows():
    return _FLOWS


def get_gather_flow(flow_id):
    return _FLOW_BY_ID.get(flow_id)


def gather_summary(flow_id, answers):
    """Build the clinician handoff summary for a finished gather flow."""
    flow = _FLOW_BY_ID.get(flow_id)
    if flow is None:
        return None
    gathered = [
        {"question": q, "answer": (a or "").strip() or "Not answered"}
        for q, a in zip(flow["follow_ups"], list(answers) + [""] * len(flow["follow_ups"]))
    ]
    return {
        "flow_id": flow_id,
        "message": flow["message"],
        "outcome": "handoff",
        "outcome_note": "Answer-or-handoff rules come from Experiment 3. Until then, every path ends in a clinician handoff.",
        "gathered": gathered,
    }


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = set()
    for word in words:
        if len(word) > 3 and word.endswith("s"):
            word = word[:-1]
        if len(word) > 2 and word not in _STOP:
            out.add(word)
    return out


def _score(asked, candidate_text):
    candidate = _tokens(candidate_text)
    overlap = len(asked & candidate)
    if not overlap:
        return 0.0
    if overlap < 2 and len(asked) > 2:
        return 0.0
    return overlap / min(len(asked), len(candidate)) + overlap / 100


def match(text):
    """Find the closest gather flow or bank question for free text.

    Returns ("flow", flow), ("question", presented question) or (None, None).
    A flow wins a tie, so symptom messages start information gathering.
    """
    asked = _tokens(text)
    if not asked:
        return None, None
    best_kind, best_item, best_score = None, None, 0.0
    for flow in _FLOWS:
        score = _score(asked, flow["message"])
        if score > best_score:
            best_kind, best_item, best_score = "flow", flow, score
    for question in _BANK["questions"]:
        score = _score(asked, question["question"])
        if score > best_score:
            best_kind, best_item, best_score = "question", question, score
    if best_score < 0.4:
        return None, None
    if best_kind == "question":
        if best_item["gather_flow"]:
            return "flow", _FLOW_BY_ID[best_item["gather_flow"]]
        return "question", present(best_item)
    return best_kind, best_item
