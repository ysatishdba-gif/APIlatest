# Outcome 4 experiment front end (prototype)

A FastAPI front end for the three Outcome 4 discovery experiments on spine
surgery in-basket questions. All routes live under `/pre-surgery`.

Seed data only, taken from the experiment proposal. No patient data.
Not for patient use.

## The five screens

1. **Anticipate**: pick a care trigger, see the top five questions for it.
2. **Answer**: pick a question, see how it is handled (cluster A, B or C) and its answer.
3. **Gather**: pick a symptom or medication message, answer the follow-up questions, get a clinician handoff summary.
4. **Ask your own**: type a question, see the closest match in the question bank.
5. **Chat**: a conversation that answers, hands off, or asks follow-up questions.

## Run locally

```
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
uvicorn main:app --port 8080
```

Open http://localhost:8080/pre-surgery

## Files

- `main.py`: routes
- `data.py`: reads the two data files, decides handling, matches free text
- `chat.html`: the five-screen page
- `question_bank.json`: the 45 Appendix A questions
- `gather_flows.json`: the 12 Experiment 3 follow-up flows
- `tests/test_app.py`: unit tests

## How experiment results get in

Fill in these fields in `question_bank.json`. Nothing else changes.

| Field | Filled by | Meaning |
|---|---|---|
| `intent`, `acuity` | Experiment 1 | Tags for the question |
| `frequency` | Experiment 1 | Used to rank the top five per trigger |
| `cluster` | Clinician review | `A`, `B`, `C`, or a mix. A mix takes the most restrictive reading |
| `answer`, `answer_rating` | Experiment 2 | Shown only for cluster A and B questions |

Clusters and trigger mappings in the seed data are starting hypotheses from
the proposal. Cluster C, routing, unassigned and out-of-scope questions never
show an AI answer. Every gather flow ends in a clinician handoff until
Experiment 3 defines the answer-or-handoff rules.
