from pathlib import Path

import pytest

from app.game import (
    GameError,
    advance_final_timers,
    buzz,
    calculate_final_winners,
    create_game_state,
    join_team,
    load_game_config,
    reveal_final,
    score_current_clue,
    score_final,
    select_clue,
    set_final_wagers,
    submit_final_wager,
    advance_final_reveal,
)


def test_load_game_config() -> None:
    config = load_game_config("games/example.yaml")
    assert config.title == "Cybersecurity Jeopardy"
    assert len(config.teams) == 3
    assert config.final.answer == "Least privilege"


def test_regular_scoring_and_buzzer_lockout() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST1")
    clue = next(clue for category in state.categories for clue in category.clues if not clue.is_daily_double)

    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    buzz(state, "Blue Team", "Ben")

    assert state.buzz_winner == "Red Team"
    assert state.buzz_winner_student == "Alice"
    assert state.last_buzz_team == "Red Team"
    assert state.last_buzz_student == "Alice"
    assert state.buzz_expires_at is not None
    assert state.buzz_order == ["Red Team", "Blue Team"]

    score_current_clue(state, correct=True)

    red = next(team for team in state.teams if team.name == "Red Team")
    assert red.score == clue.value
    assert state.score_event is not None
    assert state.score_event.team_name == "Red Team"
    assert state.score_event.student_name == "Alice"
    assert state.score_event.delta == clue.value
    assert clue.revealed is True
    assert state.current_clue_id is None
    assert state.buzz_expires_at is None
    assert state.last_buzz_team == "Red Team"
    assert state.last_buzz_student == "Alice"


def test_daily_double_requires_wager() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST2")
    clue = next(clue for category in state.categories for clue in category.clues if clue.is_daily_double)

    select_clue(state, clue.id, selecting_team="Blue Team")

    with pytest.raises(GameError):
        buzz(state, "Red Team")

    with pytest.raises(GameError):
        score_current_clue(state, correct=True)

    score_current_clue(state, correct=False, team_name="Blue Team", wager=300)
    blue = next(team for team in state.teams if team.name == "Blue Team")
    assert blue.score == -300
    assert state.score_event is not None
    assert state.score_event.delta == -300
    assert clue.revealed is True


def test_student_buzzer_rotation_resets_after_team_cycle() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST4")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]

    join_team(state, "Red Team", "Alice")
    join_team(state, "Red Team", "Ari")

    select_clue(state, clues[0].id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False, team_name="Red Team")

    select_clue(state, clues[1].id)
    with pytest.raises(GameError):
        buzz(state, "Red Team", "Alice")
    buzz(state, "Red Team", "Ari")
    score_current_clue(state, correct=False, team_name="Red Team")

    select_clue(state, clues[2].id)
    buzz(state, "Red Team", "Alice")
    assert state.buzz_winner_student == "Alice"


def test_final_uses_joined_student_teams_only() -> None:
    state = create_game_state(load_game_config("games/final_smoke.yaml"), session_code="TEST5")
    join_team(state, "Red Team", "Alice")
    reveal_final(state)

    assert state.phase == "final_wager"
    assert state.final.leaders == {"Red Team": "Alice"}

    state.final.wager_expires_at = 0
    advance_final_timers(state)
    assert state.phase == "final_answer"
    assert state.final.wagers == {"Red Team": 500}


def test_final_wager_allows_500_floor_for_low_scores() -> None:
    state = create_game_state(load_game_config("games/final_smoke.yaml"), session_code="TEST6")
    join_team(state, "Red Team", "Alice")
    red = next(team for team in state.teams if team.name == "Red Team")
    red.score = -200
    reveal_final(state)

    submit_final_wager(state, "Red Team", "Alice", 500)
    assert state.final.wagers["Red Team"] == 500

    state = create_game_state(load_game_config("games/final_smoke.yaml"), session_code="TEST7")
    join_team(state, "Red Team", "Alice")
    reveal_final(state)
    with pytest.raises(GameError):
        submit_final_wager(state, "Red Team", "Alice", 501)


def test_final_jeopardy_scoring_once() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST3")
    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    join_team(state, "Purple Team", "Pat")
    reveal_final(state)
    assert state.phase == "final_wager"
    set_final_wagers(state, {"Red Team": 500, "Blue Team": 200, "Purple Team": 0})
    reveal_final(state)
    assert state.phase == "final_answer"
    score_final(state, {"Red Team": True, "Blue Team": False, "Purple Team": True})
    calculate_final_winners(state)
    calculate_final_winners(state)

    scores = {team.name: team.score for team in state.teams}
    assert scores == {"Red Team": 0, "Blue Team": 0, "Purple Team": 0}
    assert state.final.final_scores == {"Red Team": 500, "Blue Team": -200, "Purple Team": 0}
    assert state.final.reveal_order == ["Blue Team", "Purple Team", "Red Team"]
    advance_final_reveal(state)
    assert state.final.reveal_spinning_index == 0
    assert state.final.reveal_step == 0
    advance_final_reveal(state)
    assert state.final.reveal_spinning_index is None
    assert state.final.reveal_step == 1
    assert state.phase == "complete"


def test_invalid_yaml_reports_error(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("title: Missing pieces\n", encoding="utf-8")

    with pytest.raises(GameError):
        load_game_config(bad_yaml)
