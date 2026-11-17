# Pre-surgery and Going Home companion (prototype)

A FastAPI chat prototype. All routes live under `/pre-surgery`.
It uses made-up sample data only: two demo patients and placeholder answers.
It is not for patient use.

## Run locally

```
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
uvicorn main:app --port 8080
```

Open http://localhost:8080/pre-surgery

## Files

- `main.py`: routes (chat page, health check, patients, questions, chat)
- `data.py`: sample data layer; the only file to change when real data access is added
- `chat.html`: the chat page
- `tests/test_app.py`: unit tests
