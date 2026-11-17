"""Pre-surgery / Going Home companion. All routes live under /pre-surgery."""
import os
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import data

BASE_PATH = "/pre-surgery"
CHAT_PAGE = Path(__file__).parent / "chat.html"

app = FastAPI(title="pre-surgery-app")
router = APIRouter(prefix=BASE_PATH)


class ChatRequest(BaseModel):
    patient_key: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


def _state_or_404(patient_key):
    state = data.get_journey_state(patient_key)
    if state is None:
        raise HTTPException(status_code=404, detail="Unknown patient key")
    return state


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def chat_page():
    return CHAT_PAGE.read_text(encoding="utf-8")


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/patients")
def patients():
    return {"patients": data.list_patients()}


@router.get("/api/questions")
def questions(patient_key: str):
    state = _state_or_404(patient_key)
    return {"journey_state": state, "questions": data.get_common_questions(state)}


@router.post("/api/chat")
def chat(request: ChatRequest):
    state = _state_or_404(request.patient_key)
    return {"journey_state": state, "answer": data.get_answer(request.message, state)}


app.include_router(router)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
