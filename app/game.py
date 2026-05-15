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

FINAL_WAGER_SECONDS = 60
FINAL_ANSWER_SECONDS = 300
DAILY_DOUBLE_ANSWER_SECONDS = 60


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


def normalize_team_names(team_names: list[str]) -> list[str]:
    names = [" ".join(name.strip().split()) for name in team_names]
    names = [name for name in names if name]
    if len(names) < 2 or len(names) > 10:
        raise GameError("games require between 2 and 10 teams")
    normalized = [name.lower() for name in names]
    if len(set(normalized)) != len(normalized):
        raise GameError("team names must be unique")
    return names


def create_game_state(
    config: GameConfig,
    session_code: Optional[str] = None,
    team_names: Optional[list[str]] = None,
    owner_name: Optional[str] = None,
) -> GameState:
    teams = normalize_team_names(team_names if team_names is not None else config.teams)
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
        if clue.is_daily_double and config.daily_double:
            clue.prompt = config.daily_double.prompt
            clue.answer = config.daily_double.answer

    return GameState(
        session_code=session_code or generate_session_code(),
        title=config.title,
        owner_name=owner_name,
        teams=[TeamState(name=name) for name in teams],
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
        active_teams = active_final_team_names(state)
        if len(payload["final"]["wagers"]) < len(active_teams):
            payload["final"]["wagers"] = {}
            payload["final"]["wager_values_hidden"] = True
        if not payload["final"]["results_revealed"]:
            payload["final"]["scored"] = {}
    return payload


def all_board_clues_revealed(state: GameState) -> bool:
    return all(clue.revealed for clue in iter_clues(state.categories))


def active_final_teams(state: GameState) -> list[TeamState]:
    teams = [team for team in state.teams if team.students]
    return teams or state.teams


def active_final_team_names(state: GameState) -> set[str]:
    return {team.name for team in active_final_teams(state)}


def available_wager(team: TeamState) -> int:
    return team.score if team.score >= 500 else 500


def pick_final_leader(team: TeamState) -> Optional[str]:
    if not team.students:
        return None
    max_correct = max((team.correct_counts.get(student, 0) for student in team.students), default=0)
    candidates = [
        student
        for student in team.students
        if team.correct_counts.get(student, 0) == max_correct
    ]
    return random.choice(candidates)


def start_final_wager_phase(state: GameState) -> GameState:
    now = time.time()
    state.phase = "final_wager"
    state.current_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    state.steal_eligible = False
    state.steal_active = False
    state.steal_lockout_team = None
    state.daily_double.active = False
    state.final.phase = "wager"
    state.final.revealed = False
    state.final.wager_started_at = now
    state.final.wager_expires_at = now + FINAL_WAGER_SECONDS
    state.final.answer_started_at = None
    state.final.answer_expires_at = None
    state.final.leaders = {
        team.name: leader
        for team in active_final_teams(state)
        if (leader := pick_final_leader(team))
    }
    return state


def ensure_final_wagers(state: GameState) -> None:
    for team in active_final_teams(state):
        if team.name not in state.final.wagers:
            state.final.wagers[team.name] = available_wager(team)
            state.final.wager_submitted_by[team.name] = "Timer"


def begin_final_answer_phase(state: GameState) -> GameState:
    if state.final.phase == "pending":
        start_final_wager_phase(state)
    if state.final.phase not in {"wager", "answer"}:
        raise GameError("Final Jeopardy answers cannot be started now")
    ensure_final_wagers(state)
    now = time.time()
    state.phase = "final_answer"
    state.final.phase = "answer"
    state.final.revealed = True
    state.final.answer_started_at = now
    state.final.answer_expires_at = now + FINAL_ANSWER_SECONDS
    return state


def join_team(state: GameState, team_name: str, student_name: str = "") -> GameState:
    team = find_team(state, team_name)
    team.joined = True
    student = normalize_student_name(student_name)
    existing = {name.lower() for name in team.students}
    if student.lower() not in existing:
        team.students.append(student)
    team.correct_counts.setdefault(student, 0)
    return state


def remove_student(state: GameState, team_name: str, student_name: str) -> GameState:
    team = find_team(state, team_name)
    student = normalize_student_name(student_name)
    team.students = [name for name in team.students if name.lower() != student.lower()]
    team.answered_students = [
        name for name in team.answered_students if name.lower() != student.lower()
    ]
    team.correct_counts.pop(student, None)
    for name in list(team.correct_counts):
        if name.lower() == student.lower():
            team.correct_counts.pop(name, None)
    team.joined = bool(team.students)
    if state.buzz_winner == team_name and (state.buzz_winner_student or "").lower() == student.lower():
        state.buzz_winner = None
        state.buzz_winner_student = None
        state.buzz_expires_at = None
    if state.last_buzz_team == team_name and (state.last_buzz_student or "").lower() == student.lower():
        state.last_buzz_team = None
        state.last_buzz_student = None
    return state


def select_clue(state: GameState, clue_id: str, selecting_team: Optional[str] = None) -> GameState:
    if state.phase != "board":
        raise GameError("clues can only be selected during board play")
    if (
        state.current_clue_id
        and state.current_clue_id != clue_id
        and (state.steal_eligible or state.steal_active)
    ):
        finish_current_clue(state, find_clue(state, state.current_clue_id))
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
    state.steal_eligible = False
    state.steal_active = False
    state.steal_lockout_team = None
    if clue.is_daily_double:
        state.daily_double.active = True
        state.daily_double.team_name = selecting_team
        state.daily_double.wager = None
        state.daily_double.question_revealed = False
        state.daily_double.answer_expires_at = None
    else:
        state.daily_double.active = False
        state.daily_double.team_name = None
        state.daily_double.wager = None
        state.daily_double.question_revealed = False
        state.daily_double.answer_expires_at = None
    return state


def lock_daily_double_wager(
    state: GameState,
    team_name: str,
    wager: int,
) -> GameState:
    if state.phase != "board":
        raise GameError("Daily Double wagers can only be locked during board play")
    if not state.current_clue_id:
        raise GameError("no clue is currently active")
    clue = find_clue(state, state.current_clue_id)
    if not clue.is_daily_double or not state.daily_double.active:
        raise GameError("the current clue is not an active Daily Double")
    find_team(state, team_name)
    if wager < 0:
        raise GameError("wager must be non-negative")
    state.daily_double.team_name = team_name
    state.daily_double.wager = wager
    state.daily_double.question_revealed = True
    state.daily_double.answer_expires_at = time.time() + DAILY_DOUBLE_ANSWER_SECONDS
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
    if state.steal_active and team_name == state.steal_lockout_team:
        raise GameError(f"{team_name} is locked out of this steal")
    if student.lower() not in {name.lower() for name in team.students}:
        raise GameError(f"{student} has not joined {team_name}")
    if not state.steal_active:
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


def finish_current_clue(state: GameState, clue: ClueState) -> None:
    clue.revealed = True
    state.current_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    state.steal_eligible = False
    state.steal_active = False
    state.steal_lockout_team = None
    state.daily_double.active = False
    state.daily_double.question_revealed = False
    state.daily_double.answer_expires_at = None


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

    if not clue.is_daily_double and state.steal_active and not state.buzz_winner and not correct:
        finish_current_clue(state, clue)
        if all_board_clues_revealed(state):
            start_final_wager_phase(state)
        return state

    if clue.is_daily_double:
        scorer = team_name or state.daily_double.team_name
        if not scorer:
            raise GameError("Daily Double scoring requires a team")
        if wager is None:
            wager = state.daily_double.wager
        if wager is None:
            raise GameError("Daily Double scoring requires a wager")
        if wager < 0:
            raise GameError("wager must be non-negative")
        amount = wager
        state.daily_double.wager = wager
        state.daily_double.team_name = scorer
    else:
        scorer = state.buzz_winner or team_name
        if not scorer:
            raise GameError("regular clue scoring requires a buzz winner or team")
        amount = clue.value

    team = find_team(state, scorer)
    steal_attempt = (
        not clue.is_daily_double
        and state.steal_active
        and scorer != state.steal_lockout_team
    )
    opens_steal = (
        not clue.is_daily_double
        and not correct
        and not state.steal_active
        and state.buzz_winner == scorer
    )
    delta = amount if correct else (0 if steal_attempt else -amount)
    team.score += delta
    state.score_event_id += 1
    state.score_event = ScoreEvent(
        id=state.score_event_id,
        team_name=scorer,
        student_name=state.buzz_winner_student if state.buzz_winner == scorer else None,
        delta=delta,
        correct=correct,
        clue_value=amount,
        clue_prompt=clue.prompt,
        clue_answer=None if opens_steal else clue.answer,
    )
    if state.buzz_winner == scorer and state.buzz_winner_student:
        if not steal_attempt:
            answered = {name.lower() for name in team.answered_students}
            if state.buzz_winner_student.lower() not in answered:
                team.answered_students.append(state.buzz_winner_student)
        if correct:
            team.correct_counts[state.buzz_winner_student] = (
                team.correct_counts.get(state.buzz_winner_student, 0) + 1
            )
        if not steal_attempt and len(team.answered_students) >= len(team.students):
            team.answered_students = []
    if opens_steal:
        state.steal_eligible = False
        state.steal_active = True
        state.steal_lockout_team = scorer
        state.buzz_winner = None
        state.buzz_winner_student = None
        state.buzz_expires_at = None
        state.buzz_order = []
        return state
    finish_current_clue(state, clue)
    if all_board_clues_revealed(state):
        start_final_wager_phase(state)
    return state


def continue_current_clue(state: GameState) -> GameState:
    if state.phase != "board":
        raise GameError("board clues cannot be continued outside board play")
    if not state.current_clue_id:
        raise GameError("no clue is currently active")
    clue = find_clue(state, state.current_clue_id)
    if clue.revealed:
        raise GameError("that clue has already been scored")
    finish_current_clue(state, clue)
    if all_board_clues_revealed(state):
        start_final_wager_phase(state)
    return state


def set_team_scores(state: GameState, scores: dict[str, int]) -> GameState:
    team_names = {team.name for team in state.teams}
    unknown = set(scores) - team_names
    if unknown:
        raise GameError(f"unknown teams in scores: {', '.join(sorted(unknown))}")
    for team in state.teams:
        if team.name in scores:
            team.score = scores[team.name]
    return state


def reveal_final(state: GameState) -> GameState:
    if state.final.phase == "pending":
        return start_final_wager_phase(state)
    return begin_final_answer_phase(state)


def set_final_wagers(state: GameState, wagers: dict[str, int]) -> GameState:
    team_names = {team.name for team in state.teams}
    unknown = set(wagers) - team_names
    if unknown:
        raise GameError(f"unknown teams in wagers: {', '.join(sorted(unknown))}")
    for team_name, wager in wagers.items():
        if wager < 0:
            raise GameError(f"wager for {team_name} must be non-negative")
    state.final.wagers = dict(wagers)
    state.final.wager_submitted_by.update({team_name: "Teacher" for team_name in wagers})
    return state


def submit_final_wager(
    state: GameState,
    team_name: str,
    student_name: str,
    wager: int,
) -> GameState:
    if state.final.phase == "pending":
        start_final_wager_phase(state)
    if state.final.phase != "wager":
        raise GameError("Final Jeopardy wagers are not open")
    team = find_team(state, team_name)
    student = normalize_student_name(student_name)
    if state.final.leaders.get(team_name) != student:
        raise GameError(f"{student} is not the final leader for {team_name}")
    if time.time() > (state.final.wager_expires_at or 0):
        wager = available_wager(team)
    if wager < 0:
        raise GameError("wager must be non-negative")
    if wager > available_wager(team):
        raise GameError(
            f"wager cannot exceed ${available_wager(team)}; teams below $500 may wager up to $500"
        )
    state.final.wagers[team_name] = wager
    state.final.wager_submitted_by[team_name] = student
    if len(state.final.wagers) >= len(active_final_teams(state)):
        begin_final_answer_phase(state)
    return state


def submit_final_answer(
    state: GameState,
    team_name: str,
    student_name: str,
    answer: str,
) -> GameState:
    if state.final.phase != "answer":
        raise GameError("Final Jeopardy answers are not open")
    student = normalize_student_name(student_name)
    if state.final.leaders.get(team_name) != student:
        raise GameError(f"{student} is not the final leader for {team_name}")
    state.final.submissions[team_name] = answer.strip()
    state.final.submitted_by[team_name] = student
    if len(state.final.submissions) >= len(active_final_teams(state)):
        state.phase = "final_review"
        state.final.phase = "review"
    return state


def advance_final_timers(state: GameState) -> GameState:
    now = time.time()
    if state.final.phase == "wager" and state.final.wager_expires_at is not None:
        if now >= state.final.wager_expires_at:
            ensure_final_wagers(state)
            begin_final_answer_phase(state)
    if state.final.phase == "answer" and state.final.answer_expires_at is not None:
        if now >= state.final.answer_expires_at:
            for team in active_final_teams(state):
                if team.name not in state.final.submissions:
                    state.final.submissions[team.name] = ""
                    state.final.submitted_by[team.name] = "Timer"
            state.phase = "final_review"
            state.final.phase = "review"
    return state


def score_final(state: GameState, results: dict[str, bool]) -> GameState:
    ensure_final_wagers(state)
    for team_name, correct in results.items():
        find_team(state, team_name)
        state.final.scored[team_name] = correct
    state.phase = "final_review"
    state.final.phase = "review"
    return state


def calculate_final_winners(state: GameState) -> GameState:
    if state.final.results_revealed:
        return state
    ensure_final_wagers(state)
    for team in active_final_teams(state):
        if team.name not in state.final.submissions:
            state.final.submissions[team.name] = ""
            state.final.submitted_by[team.name] = "Timer"
        if team.name not in state.final.scored:
            state.final.scored[team.name] = False
    state.final.final_scores = {}
    for team in active_final_teams(state):
        wager = state.final.wagers.get(team.name, 0)
        correct = state.final.scored.get(team.name, False)
        state.final.final_scores[team.name] = team.score + (wager if correct else -wager)
    state.final.reveal_order = [
        team.name
        for team in sorted(
            active_final_teams(state),
            key=lambda item: state.final.final_scores.get(item.name, item.score),
        )
    ]
    state.final.reveal_step = 0
    state.final.reveal_spinning_index = None
    state.final.results_revealed = True
    state.phase = "complete"
    state.final.phase = "complete"
    return state


def advance_final_reveal(state: GameState) -> GameState:
    if state.final.phase != "complete" or not state.final.results_revealed:
        raise GameError("final results have not been calculated")
    if state.final.reveal_spinning_index is not None:
        state.final.reveal_step = max(
            state.final.reveal_step,
            state.final.reveal_spinning_index + 1,
        )
        state.final.reveal_spinning_index = None
    elif state.final.reveal_step < len(state.final.reveal_order):
        state.final.reveal_spinning_index = state.final.reveal_step
    return state
