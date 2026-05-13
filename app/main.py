from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .game import (
    GameError,
    buzz,
    create_game_state,
    join_team,
    load_game_config,
    public_state,
    reset_buzzer,
    reveal_final,
    score_current_clue,
    score_final,
    select_clue,
    set_final_wagers,
)
from .models import (
    BuzzRequest,
    FinalScoreRequest,
    FinalWagersRequest,
    JoinRequest,
    ScoreClueRequest,
    SelectClueRequest,
    SessionRequest,
    StartGameRequest,
)
from .realtime import ConnectionManager
from .store import GameStore


app = FastAPI(title="Classroom Jeopardy")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

store = GameStore(os.getenv("JEOPARDY_DB", "jeopardy.db"))
manager = ConnectionManager()


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
async def teacher_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("teacher.html", {"request": request})


@app.get("/join", response_class=HTMLResponse)
async def join_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("student.html", {"request": request})


@app.get("/student", response_class=HTMLResponse)
async def student_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("student.html", {"request": request})


@app.get("/api/game/latest")
async def latest_game() -> dict:
    state = store.latest()
    if not state:
        raise HTTPException(status_code=404, detail="no game has been started")
    return public_state(state, reveal_answers=True)


@app.get("/api/game/{session_code}")
async def get_game(session_code: str, teacher: bool = False) -> dict:
    try:
        state = store.get(session_code)
    except KeyError as exc:
        raise not_found(session_code) from exc
    return public_state(state, reveal_answers=teacher)


@app.post("/api/game/start")
async def start_game(payload: StartGameRequest) -> dict:
    try:
        config = load_game_config(payload.config_path)
        state = create_game_state(config)
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store.save(state)
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/join")
async def join(payload: JoinRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: join_team(current, payload.team_name),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return {"team_name": payload.team_name, "state": public_state(state)}


@app.post("/api/buzz")
async def buzz_route(payload: BuzzRequest) -> dict:
    try:
        state = store.update(
            payload.session_code,
            lambda current: buzz(current, payload.team_name),
        )
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    except GameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await broadcast_state(state.session_code)
    return public_state(state)


@app.post("/api/clue/select")
async def select_clue_route(payload: SelectClueRequest) -> dict:
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
async def score_clue_route(payload: ScoreClueRequest) -> dict:
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


@app.post("/api/buzzer/reset")
async def reset_buzzer_route(payload: SessionRequest) -> dict:
    try:
        state = store.update(payload.session_code, reset_buzzer)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/reveal")
async def reveal_final_route(payload: SessionRequest) -> dict:
    try:
        state = store.update(payload.session_code, reveal_final)
    except KeyError as exc:
        raise not_found(payload.session_code) from exc
    await broadcast_state(state.session_code)
    return public_state(state, reveal_answers=True)


@app.post("/api/final/wagers")
async def final_wagers_route(payload: FinalWagersRequest) -> dict:
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


@app.post("/api/final/score")
async def final_score_route(payload: FinalScoreRequest) -> dict:
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
