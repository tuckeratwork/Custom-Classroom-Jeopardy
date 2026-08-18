from __future__ import annotations

import os
import secrets
import base64
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .game import (
    GameError,
    advance_auto_random,
    advance_final_reveal,
    advance_final_timers,
    buzz,
    calculate_final_winners,
    continue_current_clue,
    create_game_state,
    join_team,
    load_game_config,
    lock_daily_double_wager,
    move_student,
    participant_membership,
    public_state,
    random_buzz_eligible,
    remove_participant,
    remove_student,
    reset_buzzer,
    reset_buzzer_rotation,
    reset_final,
    reveal_final,
    score_current_clue,
    score_final,
    select_clue,
    set_auto_random,
    set_early_buzz_penalty,
    set_global_rotation,
    set_reactions,
    set_team_scores,
    set_final_wagers,
    submit_final_answer,
    submit_final_wager,
    submit_daily_double_answer,
    submit_daily_double_wager,
    validate_reaction,
)
from .models import (
    AutoRandomRequest,
    BuzzRequest,
    DailyDoubleAnswerRequest,
    DailyDoubleStudentWagerRequest,
    DailyDoubleWagerRequest,
    FinalAnswerRequest,
    FinalScoreRequest,
    FinalWagerRequest,
    FinalWagersRequest,
    JoinRequest,
    LeaveRequest,
    MoveStudentRequest,
    ReactionRequest,
    ReactionsToggleRequest,
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
reaction_cooldowns: dict[tuple[str, str, str], float] = {}
REACTION_COOLDOWN_SECONDS = 1.5


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


@app.get("/game-assets/{asset_path:path}")
async def game_asset(asset_path: str) -> FileResponse:
    assets_root = Path("games/assets").resolve()
    requested = (assets_root / asset_path).resolve()
    try:
        requested.relative_to(assets_root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="asset not found") from exc
    if not requested.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(requested)


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
            lambda current: join_team(
                current,
                payload.team_name,
                payload.student_name,
                payload.participant_id,
            ),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    changed_session_codes: list[str] = []
    if payload.participant_id:
        for other_state in store.all():
            if other_state.session_code == state.session_code:
                continue
            if participant_membership(other_state, payload.participant_id):
                store.update(
                    other_state.session_code,
                    lambda current: remove_participant(current, payload.participant_id or ""),
                )
                changed_session_codes.append(other_state.session_code)
    for changed_session_code in changed_session_codes:
        await broadcast_state(changed_session_code)
    await broadcast_state(state.session_code)
    return {
        "team_name": payload.team_name,
        "student_name": payload.student_name,
        "state": public_state(state),
    }


@app.post("/api/leave")
async def leave(payload: LeaveRequest) -> dict:
    def leave_current(current):
        if payload.participant_id:
            membership = participant_membership(current, payload.participant_id)
            if membership:
                return remove_participant(current, payload.participant_id)
            legacy_team = next(
                (team for team in current.teams if team.name.lower() == payload.team_name.lower()),
                None,
            )
            legacy_student = next(
                (
                    name
                    for name in (legacy_team.students if legacy_team else [])
                    if name.lower() == payload.student_name.strip().lower()
                ),
                None,
            )
            if legacy_team and legacy_student and not any(
                name.lower() == legacy_student.lower()
                for name in legacy_team.student_ids
            ):
                return remove_student(current, legacy_team.name, legacy_student)
            return current
        return remove_student(current, payload.team_name, payload.student_name)

    try:
        state = store.update(payload.session_code, leave_current)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return {"ok": True}


@app.post("/api/buzz")
async def buzz_route(payload: BuzzRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: buzz(
                current,
                payload.team_name,
                payload.student_name,
                payload.participant_id,
            ),
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


@app.post("/api/daily-double/student-wager")
async def daily_double_student_wager_route(payload: DailyDoubleStudentWagerRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: submit_daily_double_wager(
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


@app.post("/api/daily-double/answer")
async def daily_double_answer_route(payload: DailyDoubleAnswerRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: submit_daily_double_answer(
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


@app.post("/api/buzzer/reset")
async def reset_buzzer_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, reset_buzzer)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/reset-rotation")
async def reset_buzzer_rotation_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, reset_buzzer_rotation)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/random")
async def random_buzzer_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, random_buzz_eligible)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/auto-random")
async def auto_random_route(payload: AutoRandomRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: set_auto_random(current, payload.enabled),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/early-penalty")
async def early_buzz_penalty_route(
    payload: AutoRandomRequest,
    teacher: str = Depends(current_teacher),
) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: set_early_buzz_penalty(current, payload.enabled),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/global-rotation")
async def global_rotation_route(
    payload: AutoRandomRequest,
    teacher: str = Depends(current_teacher),
) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: set_global_rotation(current, payload.enabled),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/buzzer/auto-random/advance")
async def auto_random_advance_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, advance_auto_random)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/reactions/toggle")
async def reactions_toggle_route(payload: ReactionsToggleRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: set_reactions(current, payload.enabled),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/reactions/send")
async def reaction_send_route(payload: ReactionRequest) -> dict:
    try:
        state = store.get(payload.session_code)
        side = validate_reaction(state, payload.team_name, payload.student_name, payload.emoji)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    cooldown_key = (
        state.session_code,
        payload.team_name.strip().lower(),
        payload.student_name.strip().lower(),
    )
    now = time.time()
    if now - reaction_cooldowns.get(cooldown_key, 0) < REACTION_COOLDOWN_SECONDS:
        raise HTTPException(status_code=429, detail="slow down before sending another reaction")
    reaction_cooldowns[cooldown_key] = now
    await manager.broadcast(
        state.session_code,
        {
            "type": "emoji_reaction",
            "emoji": payload.emoji,
            "side": side,
            "target_team": state.buzz_winner,
            "target_student": state.buzz_winner_student,
        },
    )
    return {"ok": True}


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


@app.post("/api/students/move")
async def move_student_route(
    payload: MoveStudentRequest,
    teacher: str = Depends(current_teacher),
) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: move_student(
                current,
                payload.from_team_name,
                payload.to_team_name,
                payload.student_name,
            ),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await manager.broadcast(
        state.session_code,
        {
            "type": "student_moved",
            "from_team_name": payload.from_team_name,
            "to_team_name": payload.to_team_name,
            "student_name": payload.student_name,
        },
    )
    await broadcast_state(state.session_code)
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


@app.post("/api/final/reset")
async def final_reset_route(payload: SessionRequest, teacher: str = Depends(current_teacher)) -> dict:
    try:
        state = store.update(payload.session_code, reset_final)
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
