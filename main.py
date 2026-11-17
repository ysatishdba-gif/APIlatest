"""Outcome 4 experiment front end. All routes live under /pre-surgery."""
import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import data

BASE_PATH = "/pre-surgery"
CHAT_PAGE = Path(__file__).parent / "chat.html"
NO_MATCH = "No close match in the question bank. This would be routed to the care team."

app = FastAPI(title="pre-surgery-app")
router = APIRouter(prefix=BASE_PATH)


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)


class SummaryRequest(BaseModel):
    answers: List[str] = Field(default_factory=list, max_length=20)


class ChatState(BaseModel):
    flow_id: str
    answers: List[str] = Field(default_factory=list, max_length=20)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    state: Optional[ChatState] = None


def _found(value, what):
    if value is None:
        raise HTTPException(status_code=404, detail=f"Unknown {what}")
    return value


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def page():
    return CHAT_PAGE.read_text(encoding="utf-8")


@router.get("/api/health")
def health():
    return {"status": "ok"}


# 1. Anticipate
@router.get("/api/triggers")
def triggers():
    return {"triggers": data.list_triggers()}


@router.get("/api/triggers/{trigger_id}/questions")
def trigger_questions(trigger_id: str):
    return {"questions": _found(data.questions_for_trigger(trigger_id), "trigger")}


# 2. Answer
@router.get("/api/questions")
def questions(category: Optional[str] = None):
    return {"categories": data.list_categories(), "questions": data.list_questions(category)}


@router.get("/api/questions/{question_id}")
def question(question_id: str):
    return _found(data.get_question(question_id), "question")


# 3. Gather
@router.get("/api/gather")
def gather_flows():
    return {"flows": data.list_gather_flows()}


@router.post("/api/gather/{flow_id}/summary")
def gather_summary(flow_id: str, request: SummaryRequest):
    return _found(data.gather_summary(flow_id, request.answers), "gather flow")


# 4. Ask your own
@router.post("/api/ask")
def ask(request: AskRequest):
    kind, item = data.match(request.message)
    return {"match": kind, "question": item if kind == "question" else None,
            "flow": item if kind == "flow" else None,
            "message": None if kind else NO_MATCH}


# 5. Chat
@router.post("/api/chat")
def chat(request: ChatRequest):
    if request.state:
        flow = _found(data.get_gather_flow(request.state.flow_id), "gather flow")
        answers = request.state.answers + [request.message]
        if len(answers) < len(flow["follow_ups"]):
            return {"reply": flow["follow_ups"][len(answers)], "kind": "follow_up",
                    "state": {"flow_id": flow["id"], "answers": answers}}
        return {"reply": "Thank you. This is being handed to the care team with the summary below.",
                "kind": "handoff", "state": None,
                "summary": data.gather_summary(flow["id"], answers)}
    kind, item = data.match(request.message)
    if kind == "flow":
        return {"reply": "A few questions first, so the care team has what they need. "
                         + item["follow_ups"][0],
                "kind": "follow_up", "state": {"flow_id": item["id"], "answers": []}}
    if kind == "question":
        return {"reply": item["response"], "kind": item["handling"], "state": None,
                "matched_question": item["question"], "handling_label": item["handling_label"]}
    return {"reply": NO_MATCH, "kind": "no_match", "state": None}


app.include_router(router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
