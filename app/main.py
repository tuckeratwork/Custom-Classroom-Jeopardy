from __future__ import annotations

import os
import secrets
import base64
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .game import (
    GameError,
    advance_final_reveal,
    advance_final_timers,
    buzz,
    calculate_final_winners,
    continue_current_clue,
    create_game_state,
    join_team,
    load_game_config,
    lock_daily_double_wager,
    public_state,
    remove_student,
    reset_buzzer,
    reveal_final,
    score_current_clue,
    score_final,
    select_clue,
    set_team_scores,
    set_final_wagers,
    submit_final_answer,
    submit_final_wager,
)
from .models import (
    BuzzRequest,
    DailyDoubleWagerRequest,
    FinalAnswerRequest,
    FinalScoreRequest,
    FinalWagerRequest,
    FinalWagersRequest,
    JoinRequest,
    ScoreClueRequest,
    ScoreOverrideRequest,
    SelectClueRequest,
    SessionRequest,
    StartGameRequest,
    StudentRequest,
)
from .realtime import ConnectionManager
from .store import GameStore


app = FastAPI(title="Classroom Jeopardy")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

store = GameStore(os.getenv("JEOPARDY_DB", "jeopardy.db"))
manager = ConnectionManager()


def teacher_accounts() -> dict[str, str]:
    raw = os.getenv("JEOPARDY_TEACHERS", "teacher:jeopardy")
    accounts: dict[str, str] = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        username, password = item.split(":", 1)
        username = username.strip()
        password = password.strip()
        if username:
            accounts[username] = password
    return accounts or {"teacher": "jeopardy"}


def parse_basic_auth(authorization: str | None) -> tuple[str, str] | None:
    if not authorization:
        return None
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded.strip()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if ":" not in decoded:
        return None
    username, password = decoded.split(":", 1)
    return username, password


def validate_teacher_authorization(authorization: str | None) -> str | None:
    parsed = parse_basic_auth(authorization)
    if parsed is None:
        return None
    username, password = parsed
    accounts = teacher_accounts()
    expected = accounts.get(username)
    valid = expected is not None and secrets.compare_digest(password, expected)
    return username if valid else None


async def current_teacher(authorization: str | None = Header(default=None)) -> str:
    teacher = validate_teacher_authorization(authorization)
    if teacher is None:
        raise HTTPException(
            status_code=401,
            detail="teacher login required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return teacher


def game_files() -> list[str]:
    games_dir = Path("games")
    return sorted(
        str(path)
        for path in games_dir.glob("*.yaml")
        if path.is_file()
    )


def not_found(session_code: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"unknown session: {session_code}")


async def broadcast_state(session_code: str) -> None:
    try:
        state = store.get(session_code)
    except KeyError:
        return
    await manager.broadcast(
        state.session_code,
        {"type": "state", "state": public_state(state, reveal_answers=False)},
    )


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse("/teacher")


@app.get("/teacher", response_class=HTMLResponse)
async def teacher_page(request: Request, teacher: str = Depends(current_teacher)) -> HTMLResponse:
    return templates.TemplateResponse("teacher.html", {"request": request})


@app.get("/join", response_class=HTMLResponse)
async def join_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("student.html", {"request": request})


@app.get("/student", response_class=HTMLResponse)
async def student_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("student.html", {"request": request})


@app.get("/board", response_class=HTMLResponse)
async def board_page(request: Request, teacher: str = Depends(current_teacher)) -> HTMLResponse:
    return templates.TemplateResponse("board.html", {"request": request})


@app.get("/api/game/latest")
async def latest_game(teacher: str = Depends(current_teacher)) -> dict:
    state = store.latest(teacher)
    if not state:
        raise HTTPException(status_code=404, detail="no game has been started")
    return public_state(state, reveal_answers=True)


@app.get("/api/game/configs")
async def list_game_configs(teacher: str = Depends(current_teacher)) -> dict:
    return {"configs": game_files()}


@app.get("/api/game/{session_code}")
async def get_game(
    session_code: str,
    teacher: bool = False,
    authorization: str | None = Header(default=None),
) -> dict:
    try:
        state = store.get(session_code)
    except KeyError as exc:
        raise not_found(session_code) from exc
    if teacher and validate_teacher_authorization(authorization) is None:
        raise HTTPException(
            status_code=401,
            detail="teacher login required",
            headers={"WWW-Authenticate": "Basic"},
        )
    return public_state(state, reveal_answers=teacher)


@app.post("/api/game/start")
async def start_game(payload: StartGameRequest, teacher: str = Depends(current_teacher)) -> dict:
    previous_session_code = payload.previous_session_code.upper() if payload.previous_session_code else None
    try:
        config = load_game_config(payload.config_path)
        state = create_game_state(config, team_names=payload.team_names, owner_name=teacher)
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.save(state)
    if previous_session_code and previous_session_code != state.session_code:
        await manager.broadcast(
            previous_session_code,
            {"type": "session_ended", "new_session_code": state.session_code},
        )
        store.delete(previous_session_code)
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.get("/api/game/{session_code}/teams")
async def game_teams(session_code: str) -> dict:
    try:
        state = store.get(session_code)
    except KeyError as exc:
        raise not_found(session_code) from exc
    return {"teams": [team.name for team in state.teams], "title": state.title}


@app.post("/api/join")
async def join(payload: JoinRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: join_team(current, payload.team_name, payload.student_name),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return {
        "team_name": payload.team_name,
        "student_name": payload.student_name,
        "state": public_state(state),
    }


@app.post("/api/buzz")
async def buzz_route(payload: BuzzRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: buzz(current, payload.team_name, payload.student_name),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state)


@app.post("/api/clue/select")
async def select_clue_route(payload: SelectClueRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: select_clue(current, payload.clue_id, payload.selecting_team),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/clue/score")
async def score_clue_route(payload: ScoreClueRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: score_current_clue(
                current,
                payload.correct,
                team_name=payload.team_name,
                wager=payload.wager,
            ),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/clue/continue")
async def continue_clue_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, continue_current_clue)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/daily-double/wager")
async def daily_double_wager_route(payload: DailyDoubleWagerRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: lock_daily_double_wager(
                current,
                payload.team_name,
                payload.wager,
            ),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/reset")
async def reset_buzzer_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, reset_buzzer)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/scores")
async def set_scores_route(payload: ScoreOverrideRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: set_team_scores(current, payload.scores),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/students/remove")
async def remove_student_route(payload: StudentRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: remove_student(current, payload.team_name, payload.student_name),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    await manager.broadcast(
        state.session_code,
        {
            "type": "student_removed",
            "team_name": payload.team_name,
            "student_name": payload.student_name,
        },
    )
    return public_state(state, reveal_answers=True)


@app.post("/api/final/reveal")
async def reveal_final_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, reveal_final)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/wagers")
async def final_wagers_route(payload: FinalWagersRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: set_final_wagers(current, payload.wagers),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/wager")
async def final_wager_route(payload: FinalWagerRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: submit_final_wager(
                current,
                payload.team_name,
                payload.student_name,
                payload.wager,
            ),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state)


@app.post("/api/final/answer")
async def final_answer_route(payload: FinalAnswerRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: submit_final_answer(
                current,
                payload.team_name,
                payload.student_name,
                payload.answer,
            ),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state)


@app.post("/api/final/score")
async def final_score_route(payload: FinalScoreRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: score_final(current, payload.results),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/calculate")
async def final_calculate_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, calculate_final_winners)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/advance")
async def final_advance_route(payload: SessionRequest) -> dict:
    try:
        state = store.update(payload.session_code, advance_final_timers)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/reveal-next")
async def final_reveal_next_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, advance_final_reveal)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.websocket("/ws/game/{session_code}")
async def websocket_route(websocket: WebSocket, session_code: str) -> None:
    session_code = session_code.upper()
    await manager.connect(session_code, websocket)
    try:
        try:
            state = store.get(session_code)
            await websocket.send_json({"type": "state", "state": public_state(state)})
        except KeyError:
            await websocket.send_json({"type": "error", "detail": "unknown session"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(session_code, websocket)
