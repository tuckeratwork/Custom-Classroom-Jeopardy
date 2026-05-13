from __future__ import annotations

import random
import secrets
import string
import time
from pathlib import Path
from typing import Iterable, Optional

import yaml
from pydantic import ValidationError

from .models import (
    CategoryState,
    ClueState,
    DailyDoubleState,
    FinalState,
    GameConfig,
    GameState,
    ScoreEvent,
    TeamState,
)


class GameError(ValueError):
    """Raised when a game action is invalid for the current state."""


def load_game_config(path: str | Path) -> GameConfig:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GameError("game YAML must contain an object at the top level")
    try:
        return GameConfig.model_validate(data)
    except ValidationError as exc:
        raise GameError(str(exc)) from exc


def generate_session_code(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def create_game_state(config: GameConfig, session_code: Optional[str] = None) -> GameState:
    categories = []
    clue_ids = []
    for category_index, category in enumerate(config.categories):
        clues = []
        for clue_index, clue in enumerate(category.clues):
            clue_id = f"c{category_index}_q{clue_index}"
            clue_ids.append(clue_id)
            clues.append(
                ClueState(
                    id=clue_id,
                    value=clue.value,
                    prompt=clue.prompt,
                    answer=clue.answer,
                )
            )
        categories.append(CategoryState(name=category.name, clues=clues))

    daily_double_id = random.choice(clue_ids)
    for clue in iter_clues(categories):
        clue.is_daily_double = clue.id == daily_double_id

    return GameState(
        session_code=session_code or generate_session_code(),
        title=config.title,
        teams=[TeamState(name=name) for name in config.teams],
        categories=categories,
        final=FinalState(
            category=config.final.category,
            prompt=config.final.prompt,
            answer=config.final.answer,
        ),
        daily_double=DailyDoubleState(clue_id=daily_double_id),
    )


def iter_clues(categories: Iterable[CategoryState]) -> Iterable[ClueState]:
    for category in categories:
        yield from category.clues


def find_team(state: GameState, team_name: str) -> TeamState:
    for team in state.teams:
        if team.name == team_name:
            return team
    raise GameError(f"unknown team: {team_name}")


def normalize_student_name(student_name: str) -> str:
    normalized = " ".join(student_name.strip().split())
    if not normalized:
        raise GameError("student name is required")
    return normalized


def find_clue(state: GameState, clue_id: str) -> ClueState:
    for clue in iter_clues(state.categories):
        if clue.id == clue_id:
            return clue
    raise GameError(f"unknown clue: {clue_id}")


def public_state(state: GameState, reveal_answers: bool = False) -> dict:
    payload = state.model_dump()
    if not reveal_answers:
        for category in payload["categories"]:
            for clue in category["clues"]:
                clue.pop("answer", None)
        payload["final"].pop("answer", None)
    return payload


def join_team(state: GameState, team_name: str, student_name: str = "") -> GameState:
    team = find_team(state, team_name)
    team.joined = True
    student = normalize_student_name(student_name)
    existing = {name.lower() for name in team.students}
    if student.lower() not in existing:
        team.students.append(student)
    return state


def select_clue(state: GameState, clue_id: str, selecting_team: Optional[str] = None) -> GameState:
    if state.phase != "board":
        raise GameError("clues can only be selected during board play")
    clue = find_clue(state, clue_id)
    if clue.revealed:
        raise GameError("that clue has already been revealed")
    if selecting_team:
        find_team(state, selecting_team)
    state.current_clue_id = clue_id
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    if clue.is_daily_double:
        state.daily_double.active = True
        state.daily_double.team_name = selecting_team
        state.daily_double.wager = None
    else:
        state.daily_double.active = False
        state.daily_double.team_name = None
        state.daily_double.wager = None
    return state


def buzz(state: GameState, team_name: str, student_name: str = "") -> GameState:
    team = find_team(state, team_name)
    student = normalize_student_name(student_name)
    if state.phase != "board":
        raise GameError("buzzing is only available during board play")
    if not state.current_clue_id:
        raise GameError("no clue is currently active")
    if state.daily_double.active:
        raise GameError("buzzing is disabled for Daily Double clues")
    if student.lower() not in {name.lower() for name in team.students}:
        raise GameError(f"{student} has not joined {team_name}")
    if len(team.answered_students) >= len(team.students):
        team.answered_students = []
    if student.lower() in {name.lower() for name in team.answered_students}:
        raise GameError(f"{student} has already answered this team cycle")
    if team_name not in state.buzz_order:
        state.buzz_order.append(team_name)
    if state.buzz_winner is None:
        state.buzz_winner = team_name
        state.buzz_winner_student = student
        state.buzz_expires_at = time.time() + 10
        state.last_buzz_team = team_name
        state.last_buzz_student = student
    return state


def reset_buzzer(state: GameState) -> GameState:
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    return state


def score_current_clue(
    state: GameState,
    correct: bool,
    team_name: Optional[str] = None,
    wager: Optional[int] = None,
) -> GameState:
    if state.phase != "board":
        raise GameError("board clues cannot be scored outside board play")
    if not state.current_clue_id:
        raise GameError("no clue is currently active")

    clue = find_clue(state, state.current_clue_id)
    if clue.revealed:
        raise GameError("that clue has already been scored")

    if clue.is_daily_double:
        scorer = team_name or state.daily_double.team_name
        if not scorer:
            raise GameError("Daily Double scoring requires a team")
        if wager is None:
            raise GameError("Daily Double scoring requires a wager")
        if wager < 0:
            raise GameError("wager must be non-negative")
        amount = wager
        state.daily_double.wager = wager
        state.daily_double.team_name = scorer
    else:
        scorer = team_name or state.buzz_winner
        if not scorer:
            raise GameError("regular clue scoring requires a buzz winner or team")
        amount = clue.value

    team = find_team(state, scorer)
    delta = amount if correct else -amount
    team.score += delta
    state.score_event_id += 1
    state.score_event = ScoreEvent(
        id=state.score_event_id,
        team_name=scorer,
        student_name=state.buzz_winner_student if state.buzz_winner == scorer else None,
        delta=delta,
        correct=correct,
    )
    if state.buzz_winner == scorer and state.buzz_winner_student:
        answered = {name.lower() for name in team.answered_students}
        if state.buzz_winner_student.lower() not in answered:
            team.answered_students.append(state.buzz_winner_student)
        if len(team.answered_students) >= len(team.students):
            team.answered_students = []
    clue.revealed = True
    state.current_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    state.daily_double.active = False
    return state


def reveal_final(state: GameState) -> GameState:
    state.phase = "final"
    state.current_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    state.final.revealed = True
    return state


def set_final_wagers(state: GameState, wagers: dict[str, int]) -> GameState:
    team_names = {team.name for team in state.teams}
    unknown = set(wagers) - team_names
    if unknown:
        raise GameError(f"unknown teams in wagers: {', '.join(sorted(unknown))}")
    for team_name, wager in wagers.items():
        if wager < 0:
            raise GameError(f"wager for {team_name} must be non-negative")
    state.final.wagers = dict(wagers)
    return state


def score_final(state: GameState, results: dict[str, bool]) -> GameState:
    if not state.final.wagers:
        raise GameError("final wagers must be entered before scoring")
    for team_name, correct in results.items():
        if team_name in state.final.scored:
            continue
        team = find_team(state, team_name)
        wager = state.final.wagers.get(team_name, 0)
        team.score += wager if correct else -wager
        state.final.scored[team_name] = correct
    if len(state.final.scored) >= len(state.teams):
        state.phase = "complete"
    return state
