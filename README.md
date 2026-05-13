# Classroom Jeopardy

A LAN-hosted Jeopardy game for classroom use. The teacher runs the FastAPI app, students join from a browser on the same Wi-Fi, and the first buzzer press locks the clue until the teacher scores or resets it.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Teacher view:

```text
http://localhost:8000/teacher
```

Student join URL:

```text
http://TEACHER_LAN_IP:8000/join
```

On Linux, you can usually find the LAN IP with:

```bash
hostname -I
```

## Editing A Game

Edit `games/example.yaml` or create another YAML file with the same shape. In the teacher console, enter the YAML path before pressing **Start game**.

Numeric-looking answers such as `404` can be written with or without quotes.

## Tests

```bash
.venv/bin/pytest -q
```
