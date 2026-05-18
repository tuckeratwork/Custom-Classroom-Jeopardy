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

## Teacher Accounts

Teacher pages and teacher-only actions require login. If no accounts are configured, the default development login is:

```text
teacher / jeopardy
```

To add one or more teacher accounts, set `JEOPARDY_TEACHERS` before starting the app. Use comma-separated `username:password` pairs:

```bash
JEOPARDY_TEACHERS="alice:password1,bob:password2" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Each teacher account can run its own game session.

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
