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
FINAL_ANSWER_GRACE_SECONDS = 5
DAILY_DOUBLE_ANSWER_SECONDS = 60
AUTO_RANDOM_SECONDS = 15
MANUAL_BUZZ_SECONDS = 10
RANDOM_BUZZ_SECONDS = 15
EARLY_BUZZ_SECONDS = 5
FINAL_ELIGIBLE_BUZZ_SECONDS = 20


class GameError(ValueError):
    """Raised when a game action is invalid for the current state."""


def load_game_config(path: str | Path) -> GameConfig:
    config_path = Path(path)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GameError(f"could not read game YAML {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise GameError(f"could not parse game YAML {config_path}: {exc}") from exc
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
                    image=clue.image,
                )
            )
        categories.append(CategoryState(name=category.name, clues=clues))

    daily_double_id = random.choice(clue_ids)
    for clue in iter_clues(categories):
        clue.is_daily_double = clue.id == daily_double_id
        if clue.is_daily_double and config.daily_double:
            clue.prompt = config.daily_double.prompt
            clue.answer = config.daily_double.answer
            clue.image = None

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


def contains_student(students: Iterable[str], student_name: str) -> bool:
    target = student_name.lower()
    return any(name.lower() == target for name in students)


def remove_student_name(students: list[str], student_name: str) -> list[str]:
    target = student_name.lower()
    return [name for name in students if name.lower() != target]


def student_count_value(counts: dict[str, int], student_name: str) -> int:
    target = student_name.lower()
    return sum(value for name, value in counts.items() if name.lower() == target)


def remove_student_count(counts: dict[str, int], student_name: str) -> None:
    target = student_name.lower()
    for name in list(counts):
        if name.lower() == target:
            counts.pop(name, None)


def student_participant_id(team: TeamState, student_name: str) -> Optional[str]:
    target = student_name.lower()
    return next(
        (participant_id for name, participant_id in team.student_ids.items() if name.lower() == target),
        None,
    )


def remove_student_participant_id(team: TeamState, student_name: str) -> None:
    target = student_name.lower()
    for name in list(team.student_ids):
        if name.lower() == target:
            team.student_ids.pop(name, None)


def participant_membership(state: GameState, participant_id: str) -> Optional[tuple[str, str]]:
    for team in state.teams:
        for student_name, stored_id in team.student_ids.items():
            if stored_id == participant_id and contains_student(team.students, student_name):
                return team.name, student_name
    return None


def reset_global_rotation_if_complete(state: GameState) -> bool:
    connected = [team for team in state.teams if team.students]
    if not state.global_rotation_enabled or not connected:
        return False
    if not all(
        all(contains_student(team.answered_students, student) for student in team.students)
        for team in connected
    ):
        return False
    for team in state.teams:
        team.answered_students = []
    return True


def find_clue(state: GameState, clue_id: str) -> ClueState:
    for clue in iter_clues(state.categories):
        if clue.id == clue_id:
            return clue
    raise GameError(f"unknown clue: {clue_id}")


def public_state(state: GameState, reveal_answers: bool = False) -> dict:
    payload = state.model_dump()
    payload["final"].pop("answer_drafts", None)
    payload["final"].pop("answer_draft_versions", None)
    if not reveal_answers:
        for category in payload["categories"]:
            for clue in category["clues"]:
                clue.pop("answer", None)
        payload["final"].pop("answer", None)
        if payload["final"].get("phase") in {"pending", "wager"}:
            payload["final"]["category"] = "Final Jeopardy"
            payload["final"]["prompt"] = ""
        active_teams = active_final_team_names(state)
        if len(payload["final"]["wagers"]) < len(active_teams):
            payload["final"]["wagers"] = {}
            payload["final"]["wager_values_hidden"] = True
        if not payload["final"]["results_revealed"]:
            payload["final"]["scored"] = {}
        if payload["daily_double"].get("submission"):
            payload["daily_double"]["submission"] = "Submitted"
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


def validate_wager_limit(team: TeamState, wager: int) -> None:
    limit = available_wager(team)
    if wager < 0:
        raise GameError("wager must be non-negative")
    if wager > limit:
        raise GameError(
            f"wager cannot exceed ${limit}; teams below $500 may wager up to $500"
        )


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
    state.last_completed_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_selection_reason = None
    state.final_eligible_trigger_team = None
    state.final_eligible_trigger_student = None
    state.buzz_expires_at = None
    state.auto_random_expires_at = None
    state.buzz_open_at = None
    state.early_buzz_lockouts = {}
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
    state.final.wagers = {}
    state.final.wager_submitted_by = {}
    state.final.submissions = {}
    state.final.submitted_by = {}
    state.final.answer_drafts = {}
    state.final.answer_draft_versions = {}
    state.final.auto_submitted_teams = []
    state.final.scored = {}
    state.final.final_scores = {}
    state.final.reveal_order = []
    state.final.reveal_step = 0
    state.final.reveal_spinning_index = None
    state.final.results_revealed = False
    state.final.leaders = {
        team.name: leader
        for team in active_final_teams(state)
        if (leader := pick_final_leader(team))
    }
    return state


def reset_final(state: GameState) -> GameState:
    if state.final.phase == "pending" and state.phase != "complete":
        raise GameError("Final Jeopardy has not started")
    return start_final_wager_phase(state)


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


def join_team(
    state: GameState,
    team_name: str,
    student_name: str = "",
    participant_id: Optional[str] = None,
) -> GameState:
    team = find_team(state, team_name)
    student = normalize_student_name(student_name)
    participant = (participant_id or "").strip() or None
    membership = participant_membership(state, participant) if participant else None
    same_membership = bool(
        membership
        and membership[0].lower() == team.name.lower()
        and membership[1].lower() == student.lower()
    )
    if membership and not same_membership:
        previous_team_name, previous_student_name = membership
        if previous_team_name.lower() != team.name.lower():
            move_student(state, previous_team_name, team.name, previous_student_name)
            team = find_team(state, team.name)
        if previous_student_name.lower() != student.lower():
            remove_student(state, team.name, previous_student_name)
    team.joined = True
    existing_student = next(
        (name for name in team.students if name.lower() == student.lower()),
        None,
    )
    if existing_student:
        student = existing_student
    else:
        team.students.append(student)
    team.correct_counts.setdefault(student, 0)
    if participant:
        remove_student_participant_id(team, student)
        team.student_ids[student] = participant
    return state


def remove_participant(state: GameState, participant_id: str) -> GameState:
    participant = participant_id.strip()
    if not participant:
        return state
    while membership := participant_membership(state, participant):
        team_name, student_name = membership
        remove_student(state, team_name, student_name)
    return state


def move_student(
    state: GameState,
    from_team_name: str,
    to_team_name: str,
    student_name: str,
) -> GameState:
    if state.phase != "board" or state.daily_double.active:
        raise GameError("students cannot be moved during Daily Double or Final Jeopardy")
    if from_team_name == to_team_name:
        raise GameError("student is already on that team")
    source = find_team(state, from_team_name)
    destination = find_team(state, to_team_name)
    student = normalize_student_name(student_name)
    if not contains_student(source.students, student):
        raise GameError(f"{student} has not joined {from_team_name}")
    if contains_student(destination.students, student):
        raise GameError(f"{student} is already on {to_team_name}")

    answered = contains_student(source.answered_students, student)
    correct_count = student_count_value(source.correct_counts, student)
    participant_id = student_participant_id(source, student)
    source.students = remove_student_name(source.students, student)
    source.answered_students = remove_student_name(source.answered_students, student)
    remove_student_count(source.correct_counts, student)
    remove_student_participant_id(source, student)
    source.joined = bool(source.students)

    destination.students.append(student)
    destination.joined = True
    destination.correct_counts[student] = destination.correct_counts.get(student, 0) + correct_count
    if participant_id:
        destination.student_ids[student] = participant_id
    if answered and not contains_student(destination.answered_students, student):
        destination.answered_students.append(student)

    source_lockouts = state.early_buzz_lockouts.get(from_team_name, [])
    if contains_student(source_lockouts, student):
        state.early_buzz_lockouts[from_team_name] = remove_student_name(source_lockouts, student)
        destination_lockouts = state.early_buzz_lockouts.setdefault(to_team_name, [])
        if not contains_student(destination_lockouts, student):
            destination_lockouts.append(student)
    state.early_buzz_lockouts = {
        team_name: students
        for team_name, students in state.early_buzz_lockouts.items()
        if students
    }

    active_buzz_moved = (
        state.buzz_winner == from_team_name
        and (state.buzz_winner_student or "").lower() == student.lower()
    )
    if active_buzz_moved:
        state.buzz_winner = to_team_name
        state.buzz_order = list(dict.fromkeys(
            to_team_name if name == from_team_name else name
            for name in state.buzz_order
        ))
    if (
        state.final_eligible_trigger_team == from_team_name
        and (state.final_eligible_trigger_student or "").lower() == student.lower()
    ):
        state.final_eligible_trigger_team = to_team_name
    if (
        state.last_buzz_team == from_team_name
        and (state.last_buzz_student or "").lower() == student.lower()
    ):
        state.last_buzz_team = to_team_name
    if (
        state.steal_active
        and state.steal_lockout_team == from_team_name
        and state.score_event
        and (state.score_event.student_name or "").lower() == student.lower()
    ):
        state.steal_lockout_team = to_team_name
    reset_global_rotation_if_complete(state)
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
    remove_student_participant_id(team, student)
    team.joined = bool(team.students)
    if team_name in state.early_buzz_lockouts:
        state.early_buzz_lockouts[team_name] = remove_student_name(
            state.early_buzz_lockouts[team_name], student
        )
        if not state.early_buzz_lockouts[team_name]:
            state.early_buzz_lockouts.pop(team_name, None)
    if state.buzz_winner == team_name and (state.buzz_winner_student or "").lower() == student.lower():
        state.buzz_winner = None
        state.buzz_winner_student = None
        state.buzz_selection_reason = None
        state.final_eligible_trigger_team = None
        state.final_eligible_trigger_student = None
        state.buzz_expires_at = None
    if (
        state.final_eligible_trigger_team == team_name
        and (state.final_eligible_trigger_student or "").lower() == student.lower()
    ):
        state.final_eligible_trigger_team = None
        state.final_eligible_trigger_student = None
    if state.last_buzz_team == team_name and (state.last_buzz_student or "").lower() == student.lower():
        state.last_buzz_team = None
        state.last_buzz_student = None
    reset_global_rotation_if_complete(state)
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
    state.last_completed_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_selection_reason = None
    state.final_eligible_trigger_team = None
    state.final_eligible_trigger_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    state.auto_random_expires_at = None
    state.buzz_open_at = None
    state.early_buzz_lockouts = {}
    state.steal_eligible = False
    state.steal_active = False
    state.steal_lockout_team = None
    if clue.is_daily_double:
        state.daily_double.active = True
        state.daily_double.team_name = state.last_buzz_team or selecting_team
        state.daily_double.leader = state.last_buzz_student if state.last_buzz_team else None
        state.daily_double.wager = None
        state.daily_double.wager_submitted_by = None
        state.daily_double.question_revealed = False
        state.daily_double.answer_expires_at = None
        state.daily_double.submission = None
        state.daily_double.submitted_by = None
    else:
        state.daily_double.active = False
        state.daily_double.team_name = None
        state.daily_double.leader = None
        state.daily_double.wager = None
        state.daily_double.wager_submitted_by = None
        state.daily_double.question_revealed = False
        state.daily_double.answer_expires_at = None
        state.daily_double.submission = None
        state.daily_double.submitted_by = None
        if state.auto_random_enabled:
            state.auto_random_expires_at = time.time() + AUTO_RANDOM_SECONDS
        if state.early_buzz_penalty_enabled:
            state.buzz_open_at = time.time() + EARLY_BUZZ_SECONDS
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
    team = find_team(state, team_name)
    validate_wager_limit(team, wager)
    state.daily_double.team_name = team_name
    state.daily_double.wager = wager
    state.daily_double.wager_submitted_by = "Teacher"
    state.daily_double.question_revealed = True
    state.daily_double.answer_expires_at = time.time() + DAILY_DOUBLE_ANSWER_SECONDS
    return state


def submit_daily_double_wager(
    state: GameState,
    team_name: str,
    student_name: str,
    wager: int,
) -> GameState:
    if state.phase != "board":
        raise GameError("Daily Double wagers can only be submitted during board play")
    if not state.current_clue_id:
        raise GameError("no clue is currently active")
    clue = find_clue(state, state.current_clue_id)
    if not clue.is_daily_double or not state.daily_double.active:
        raise GameError("the current clue is not an active Daily Double")
    student = normalize_student_name(student_name)
    if state.daily_double.team_name != team_name or state.daily_double.leader != student:
        raise GameError(f"{student} is not the Daily Double leader for {team_name}")
    team = find_team(state, team_name)
    validate_wager_limit(team, wager)
    state.daily_double.wager = wager
    state.daily_double.wager_submitted_by = student
    state.daily_double.question_revealed = True
    state.daily_double.answer_expires_at = time.time() + DAILY_DOUBLE_ANSWER_SECONDS
    return state


def submit_daily_double_answer(
    state: GameState,
    team_name: str,
    student_name: str,
    answer: str,
) -> GameState:
    if state.phase != "board":
        raise GameError("Daily Double answers can only be submitted during board play")
    if not state.current_clue_id:
        raise GameError("no clue is currently active")
    clue = find_clue(state, state.current_clue_id)
    if not clue.is_daily_double or not state.daily_double.active:
        raise GameError("the current clue is not an active Daily Double")
    student = normalize_student_name(student_name)
    if state.daily_double.team_name != team_name or state.daily_double.leader != student:
        raise GameError(f"{student} is not the Daily Double leader for {team_name}")
    if not state.daily_double.question_revealed:
        raise GameError("Daily Double wager must be locked before submitting an answer")
    if time.time() > (state.daily_double.answer_expires_at or 0):
        raise GameError("Daily Double answer timer has expired")
    state.daily_double.submission = answer.strip()
    state.daily_double.submitted_by = student
    return state


def buzz(
    state: GameState,
    team_name: str,
    student_name: str = "",
    participant_id: Optional[str] = None,
) -> GameState:
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
    stored_participant = student_participant_id(team, student)
    if participant_id and stored_participant and participant_id != stored_participant:
        raise GameError("your student session is no longer active in this room")
    if not state.steal_active:
        if state.buzz_open_at and time.time() < state.buzz_open_at:
            eligible_before = eligible_buzzers(state)
            if len(eligible_before) == 1 and eligible_before[0] == (team_name, student):
                return select_buzz_winner(
                    state,
                    team_name,
                    student,
                    FINAL_ELIGIBLE_BUZZ_SECONDS,
                    "final_eligible",
                )
            lockouts = state.early_buzz_lockouts.setdefault(team_name, [])
            if not contains_student(lockouts, student):
                lockouts.append(student)
            remaining = eligible_buzzers(state)
            if len(remaining) == 1:
                final_team, final_student = remaining[0]
                return select_buzz_winner(
                    state,
                    final_team,
                    final_student,
                    FINAL_ELIGIBLE_BUZZ_SECONDS,
                    "final_eligible",
                    trigger_team_name=team_name,
                    trigger_student_name=student,
                )
            return state
        if contains_student(state.early_buzz_lockouts.get(team_name, []), student):
            raise GameError(
                "Early buzz penalty awarded; you have been locked out from answering this question. "
                "Please do not spam the buzzer and allow the 5 second timer to finish."
            )
        if not state.global_rotation_enabled and len(team.answered_students) >= len(team.students):
            team.answered_students = []
        if student.lower() in {name.lower() for name in team.answered_students}:
            raise GameError(f"{student} has already answered this team cycle")
    if team_name not in state.buzz_order:
        state.buzz_order.append(team_name)
    if state.buzz_winner is None:
        select_buzz_winner(state, team_name, student, MANUAL_BUZZ_SECONDS, "manual")
    return state


def eligible_buzzers(state: GameState) -> list[tuple[str, str]]:
    if state.phase != "board" or not state.current_clue_id or state.daily_double.active:
        return []
    if state.buzz_winner:
        return []
    eligible: list[tuple[str, str]] = []
    for team in state.teams:
        if state.steal_active and team.name == state.steal_lockout_team:
            continue
        if not team.students:
            continue
        if state.steal_active or (
            not state.global_rotation_enabled
            and len(team.answered_students) >= len(team.students)
        ):
            students = team.students
        else:
            answered = {name.lower() for name in team.answered_students}
            early_lockouts = {
                name.lower() for name in state.early_buzz_lockouts.get(team.name, [])
            }
            students = [
                student
                for student in team.students
                if student.lower() not in answered and student.lower() not in early_lockouts
            ]
        eligible.extend((team.name, student) for student in students)
    return eligible


def reset_buzzer_rotation(state: GameState) -> GameState:
    for team in state.teams:
        team.answered_students = []
    state.early_buzz_lockouts = {}
    return reset_buzzer(state)


def select_buzz_winner(
    state: GameState,
    team_name: str,
    student_name: str,
    timer_seconds: int,
    reason: str,
    trigger_team_name: Optional[str] = None,
    trigger_student_name: Optional[str] = None,
) -> GameState:
    state.buzz_winner = team_name
    state.buzz_winner_student = student_name
    state.buzz_selection_reason = reason
    state.final_eligible_trigger_team = trigger_team_name
    state.final_eligible_trigger_student = trigger_student_name
    state.buzz_expires_at = time.time() + timer_seconds
    state.auto_random_expires_at = None
    state.buzz_open_at = None
    state.last_buzz_team = team_name
    state.last_buzz_student = student_name
    state.buzz_order = [team_name]
    return state


def random_buzz_eligible(state: GameState) -> GameState:
    choices = eligible_buzzers(state)
    if not choices:
        raise GameError("no eligible students can buzz right now")
    team_name, student = random.choice(choices)
    return select_buzz_winner(state, team_name, student, RANDOM_BUZZ_SECONDS, "random")


def set_auto_random(state: GameState, enabled: bool) -> GameState:
    state.auto_random_enabled = enabled
    state.auto_random_expires_at = None
    if enabled and state.current_clue_id and not state.buzz_winner and not state.daily_double.active:
        state.auto_random_expires_at = time.time() + AUTO_RANDOM_SECONDS
    return state


def set_early_buzz_penalty(state: GameState, enabled: bool) -> GameState:
    state.early_buzz_penalty_enabled = enabled
    state.buzz_open_at = None
    state.early_buzz_lockouts = {}
    if (
        enabled
        and state.phase == "board"
        and state.current_clue_id
        and not state.daily_double.active
        and not state.steal_active
        and not state.buzz_winner
    ):
        state.buzz_open_at = time.time() + EARLY_BUZZ_SECONDS
    return state


def set_global_rotation(state: GameState, enabled: bool) -> GameState:
    state.global_rotation_enabled = enabled
    reset_global_rotation_if_complete(state)
    return state


def set_reactions(state: GameState, enabled: bool) -> GameState:
    state.reactions_enabled = enabled
    return state


SUPPORT_REACTIONS = {"👏", "🔥", "💪", "🙌", "⭐"}
OPPOSITION_REACTIONS = {"🤔", "💀", "🍅", "💩", "🥀"}
ALL_REACTIONS = SUPPORT_REACTIONS | OPPOSITION_REACTIONS


def validate_reaction(state: GameState, team_name: str, student_name: str, emoji: str) -> str:
    if not state.reactions_enabled:
        raise GameError("reactions are disabled")
    if state.phase != "board" or not state.current_clue_id:
        raise GameError("reactions are only available during board clues")
    clue = find_clue(state, state.current_clue_id)
    if clue.is_daily_double or state.daily_double.active:
        raise GameError("reactions are disabled for Daily Double")
    if not state.buzz_winner or not state.buzz_winner_student:
        raise GameError("reactions are available after a student buzzes in")
    if emoji not in ALL_REACTIONS:
        raise GameError("unknown reaction")
    team = find_team(state, team_name)
    student = normalize_student_name(student_name)
    if student.lower() not in {name.lower() for name in team.students}:
        raise GameError(f"{student} has not joined {team_name}")
    if team_name == state.buzz_winner and student.lower() == state.buzz_winner_student.lower():
        raise GameError("the answering student cannot send reactions")
    side = "support" if team_name == state.buzz_winner else "opposition"
    if side == "support" and emoji not in SUPPORT_REACTIONS:
        raise GameError("that reaction is only available to opposing teams")
    if side == "opposition" and emoji not in OPPOSITION_REACTIONS:
        raise GameError("that reaction is only available to supporting teams")
    return side


def advance_auto_random(state: GameState) -> GameState:
    if not state.auto_random_enabled or not state.auto_random_expires_at:
        return state
    if time.time() < state.auto_random_expires_at:
        return state
    if state.buzz_winner or state.daily_double.active or not state.current_clue_id:
        state.auto_random_expires_at = None
        return state
    try:
        return random_buzz_eligible(state)
    except GameError:
        state.auto_random_expires_at = None
        return state


def reset_buzzer(state: GameState) -> GameState:
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_selection_reason = None
    state.final_eligible_trigger_team = None
    state.final_eligible_trigger_student = None
    state.buzz_expires_at = None
    state.buzz_order = []
    return state


def finish_current_clue(state: GameState, clue: ClueState) -> None:
    clue.revealed = True
    state.last_completed_clue_id = clue.id
    state.current_clue_id = None
    state.buzz_winner = None
    state.buzz_winner_student = None
    state.buzz_selection_reason = None
    state.final_eligible_trigger_team = None
    state.final_eligible_trigger_student = None
    state.buzz_expires_at = None
    state.auto_random_expires_at = None
    state.buzz_open_at = None
    state.early_buzz_lockouts = {}
    state.buzz_order = []
    state.steal_eligible = False
    state.steal_active = False
    state.steal_lockout_team = None
    state.daily_double.active = False
    state.daily_double.team_name = None
    state.daily_double.leader = None
    state.daily_double.wager = None
    state.daily_double.wager_submitted_by = None
    state.daily_double.question_revealed = False
    state.daily_double.answer_expires_at = None
    state.daily_double.submission = None
    state.daily_double.submitted_by = None


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
        if state.daily_double.wager is not None:
            wager = state.daily_double.wager
        elif wager is None:
            wager = state.daily_double.wager
        if wager is None:
            raise GameError("Daily Double scoring requires a wager")
        validate_wager_limit(find_team(state, scorer), wager)
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
        if not steal_attempt:
            if state.global_rotation_enabled:
                reset_global_rotation_if_complete(state)
            elif len(team.answered_students) >= len(team.students):
                team.answered_students = []
    if opens_steal:
        state.steal_eligible = False
        state.steal_active = True
        state.steal_lockout_team = scorer
        state.buzz_winner = None
        state.buzz_winner_student = None
        state.buzz_selection_reason = None
        state.final_eligible_trigger_team = None
        state.final_eligible_trigger_student = None
        state.buzz_expires_at = None
        state.buzz_order = []
        state.buzz_open_at = None
        state.early_buzz_lockouts = {}
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
        start_final_wager_phase(state)
        if len(state.final.wagers) >= len(active_final_teams(state)):
            return begin_final_answer_phase(state)
        return state
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
    if state.final.phase == "wager" and len(state.final.wagers) >= len(active_final_teams(state)):
        begin_final_answer_phase(state)
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
    validate_wager_limit(team, wager)
    state.final.wagers[team_name] = wager
    state.final.wager_submitted_by[team_name] = student
    if len(state.final.wagers) >= len(active_final_teams(state)):
        begin_final_answer_phase(state)
    return state


def validate_final_answerer(
    state: GameState,
    team_name: str,
    student_name: str,
    participant_id: Optional[str] = None,
) -> str:
    team = find_team(state, team_name)
    student = normalize_student_name(student_name)
    if state.final.leaders.get(team_name) != student:
        raise GameError(f"{student} is not the final leader for {team_name}")
    stored_participant = student_participant_id(team, student)
    if participant_id and stored_participant and participant_id != stored_participant:
        raise GameError("your student session is no longer active in this room")
    return student


def save_final_answer_draft(
    state: GameState,
    team_name: str,
    student_name: str,
    answer: str,
    participant_id: Optional[str] = None,
    draft_version: int = 0,
) -> GameState:
    if state.final.phase != "answer":
        raise GameError("Final Jeopardy answers are not open")
    validate_final_answerer(state, team_name, student_name, participant_id)
    if draft_version >= state.final.answer_draft_versions.get(team_name, -1):
        state.final.answer_drafts[team_name] = answer
        state.final.answer_draft_versions[team_name] = draft_version
    return state


def submit_final_answer(
    state: GameState,
    team_name: str,
    student_name: str,
    answer: str,
    participant_id: Optional[str] = None,
    automatic: bool = False,
) -> GameState:
    expires_at = state.final.answer_expires_at or 0
    if automatic and state.final.phase == "answer" and time.time() < expires_at:
        raise GameError("Final Jeopardy answer timer has not expired")
    replacing_auto_submission = (
        state.final.phase == "review"
        and team_name in state.final.auto_submitted_teams
        and time.time() <= expires_at + FINAL_ANSWER_GRACE_SECONDS
    )
    if state.final.phase != "answer" and not replacing_auto_submission:
        raise GameError("Final Jeopardy answers are not open")
    student = validate_final_answerer(state, team_name, student_name, participant_id)
    state.final.submissions[team_name] = answer.strip()
    state.final.submitted_by[team_name] = student
    state.final.answer_drafts.pop(team_name, None)
    state.final.answer_draft_versions.pop(team_name, None)
    if automatic:
        if team_name not in state.final.auto_submitted_teams:
            state.final.auto_submitted_teams.append(team_name)
    else:
        state.final.auto_submitted_teams = [
            name for name in state.final.auto_submitted_teams if name != team_name
        ]
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
                    draft = state.final.answer_drafts.pop(team.name, "").strip()
                    state.final.answer_draft_versions.pop(team.name, None)
                    state.final.submissions[team.name] = draft
                    state.final.submitted_by[team.name] = (
                        state.final.leaders.get(team.name, "Timer") if draft else "Timer"
                    )
                    if team.name not in state.final.auto_submitted_teams:
                        state.final.auto_submitted_teams.append(team.name)
            state.phase = "final_review"
            state.final.phase = "review"
    return state


def score_final(state: GameState, results: dict[str, bool]) -> GameState:
    if state.final.phase != "review":
        raise GameError("Final Jeopardy can only be scored after wagers and answers are submitted")
    active_names = active_final_team_names(state)
    missing_wagers = active_names - set(state.final.wagers)
    missing_submissions = active_names - set(state.final.submissions)
    if missing_wagers:
        raise GameError("Final Jeopardy cannot be scored until all wagers are locked")
    if missing_submissions:
        raise GameError("Final Jeopardy cannot be scored until all answers are submitted")
    for team_name, correct in results.items():
        find_team(state, team_name)
        if team_name in active_names:
            state.final.scored[team_name] = correct
    state.phase = "final_review"
    state.final.phase = "review"
    return state


def calculate_final_winners(state: GameState) -> GameState:
    if state.final.results_revealed:
        return state
    if state.final.phase != "review":
        raise GameError("Final Jeopardy results can only be calculated after answers are ready for review")
    active_names = active_final_team_names(state)
    missing_wagers = active_names - set(state.final.wagers)
    missing_submissions = active_names - set(state.final.submissions)
    missing_scores = active_names - set(state.final.scored)
    if missing_wagers:
        raise GameError("Final Jeopardy cannot be calculated until all wagers are locked")
    if missing_submissions:
        raise GameError("Final Jeopardy cannot be calculated until all answers are submitted")
    if missing_scores:
        raise GameError("Final Jeopardy cannot be calculated until every answer is marked correct or wrong")
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
