"""Sample data layer for the perioperative companions.

Everything here is made-up demo data. No real patient data.
These three functions are the only place that changes when real
ROOT / ARCHIE access is wired in.
"""

PRE_SURGERY = "pre_surgery"
POST_SURGERY = "post_surgery"

# Demo patients only. Keys are invented.
_PATIENTS = {
    "PK-DEMO-1": {"label": "Demo patient 1 (surgery scheduled)", "state": PRE_SURGERY},
    "PK-DEMO-2": {"label": "Demo patient 2 (surgery completed)", "state": POST_SURGERY},
}

# Placeholder questions and answers. Not clinical content.
_QA = {
    PRE_SURGERY: [
        ("What should I bring on the day of surgery?",
         "Sample answer: your care team will send a checklist of what to bring before your surgery date."),
        ("When should I arrive?",
         "Sample answer: your arrival time is confirmed by the surgical team before your surgery date."),
        ("Can I take my regular medications?",
         "Sample answer: medication instructions come from your care team. Please check with them before making changes."),
        ("Who do I contact with questions before surgery?",
         "Sample answer: you can message your care team through the patient portal."),
        ("How long will I be in the hospital?",
         "Sample answer: your care team will tell you the expected length of stay for your procedure."),
    ],
    POST_SURGERY: [
        ("How do I care for my incision?",
         "Sample answer: your discharge instructions describe how to care for your incision."),
        ("When can I return to normal activities?",
         "Sample answer: your care team sets activity guidance based on your procedure and recovery."),
        ("What symptoms should I report?",
         "Sample answer: your discharge instructions list the symptoms to report and who to contact."),
        ("When is my follow-up appointment?",
         "Sample answer: follow-up appointments appear in the patient portal once scheduled."),
        ("How do I manage discomfort at home?",
         "Sample answer: your care team gives you a plan for managing discomfort before you go home."),
    ],
}

FALLBACK_ANSWER = (
    "I don't have an answer for that yet. Please contact your care team with this question."
)


def list_patients():
    """Return the demo patients as a list of {key, label}."""
    return [{"key": key, "label": p["label"]} for key, p in _PATIENTS.items()]


def get_journey_state(patient_key):
    """Return 'pre_surgery' or 'post_surgery' for a patient, or None if unknown."""
    patient = _PATIENTS.get(patient_key)
    return patient["state"] if patient else None


def get_common_questions(state):
    """Return the five common questions for a journey state."""
    return [question for question, _ in _QA.get(state, [])]


def _words(text):
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in text)
    return {w for w in cleaned.split() if len(w) > 3}


def get_answer(question, state):
    """Return a canned answer for a question in the given journey state."""
    asked = _words(question)
    best_answer, best_score = FALLBACK_ANSWER, 0
    for known_question, answer in _QA.get(state, []):
        score = len(asked & _words(known_question))
        if score > best_score:
            best_answer, best_score = answer, score
    return best_answer
