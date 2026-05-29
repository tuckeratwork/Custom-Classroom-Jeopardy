from pathlib import Path

import pytest

from app.game import (
    GameError,
    advance_auto_random,
    advance_final_timers,
    buzz,
    calculate_final_winners,
    continue_current_clue,
    create_game_state,
    join_team,
    load_game_config,
    remove_student,
    random_buzz_eligible,
    reset_buzzer,
    reset_buzzer_rotation,
    reveal_final,
    score_current_clue,
    score_final,
    select_clue,
    set_auto_random,
    set_team_scores,
    set_final_wagers,
    submit_final_wager,
    advance_final_reveal,
    public_state,
)


def test_load_game_config() -> None:
    config = load_game_config("games/example.yaml")
    assert config.title == "Cybersecurity Jeopardy"
    assert len(config.teams) == 3
    assert config.final.answer == "Least privilege"


def test_game_state_can_use_runtime_team_names() -> None:
    state = create_game_state(
        load_game_config("games/example.yaml"),
        session_code="TEAMS",
        team_names=["Alpha", "Beta", "Gamma"],
        owner_name="teacher-a",
    )

    assert state.owner_name == "teacher-a"
    assert [team.name for team in state.teams] == ["Alpha", "Beta", "Gamma"]


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


def test_daily_double_config_overwrites_random_clue_content() -> None:
    state = create_game_state(load_game_config("games/cna_modules_1_5.yaml"), session_code="TEST8")
    clue = next(clue for category in state.categories for clue in category.clues if clue.is_daily_double)

    assert clue.prompt.startswith("These two branches of the US military")
    assert clue.answer == "Space Force and Marine Corps"


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


def test_teacher_can_clear_rotation_and_randomly_buzz_remaining_student() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST17")
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
    score_current_clue(state, correct=True)
    select_clue(state, clues[1].id)

    random_buzz_eligible(state)
    assert state.buzz_winner == "Red Team"
    assert state.buzz_winner_student == "Ari"

    reset_buzzer_rotation(state)
    assert next(team for team in state.teams if team.name == "Red Team").answered_students == []


def test_auto_random_starts_on_selected_clue_and_picks_if_no_buzz() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST19")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    join_team(state, "Red Team", "Alice")
    set_auto_random(state, True)

    select_clue(state, clue.id)
    assert state.auto_random_expires_at is not None
    state.auto_random_expires_at = 1
    advance_auto_random(state)

    assert state.buzz_winner == "Red Team"
    assert state.buzz_winner_student == "Alice"
    assert state.auto_random_expires_at is None


def test_auto_random_timer_clears_when_student_buzzes() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST20")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    join_team(state, "Red Team", "Alice")
    set_auto_random(state, True)
    select_clue(state, clue.id)

    buzz(state, "Red Team", "Alice")

    assert state.auto_random_expires_at is None


def test_wrong_answer_can_open_one_steal_without_rotation_or_penalty() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST9")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]

    join_team(state, "Red Team", "Alice")
    join_team(state, "Red Team", "Ari")
    join_team(state, "Blue Team", "Ben")
    join_team(state, "Blue Team", "Bea")

    select_clue(state, clues[0].id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False, team_name="Blue Team")

    red = next(team for team in state.teams if team.name == "Red Team")
    assert red.score == -clues[0].value
    assert red.answered_students == ["Alice"]
    assert state.current_clue_id == clues[0].id
    assert state.steal_active is True
    assert clues[0].revealed is False
    with pytest.raises(GameError):
        buzz(state, "Red Team", "Alice")
    with pytest.raises(GameError):
        buzz(state, "Red Team", "Ari")

    buzz(state, "Blue Team", "Ben")
    score_current_clue(state, correct=False, team_name="Red Team")

    blue = next(team for team in state.teams if team.name == "Blue Team")
    assert blue.score == 0
    assert blue.answered_students == []
    assert clues[0].revealed is True
    assert state.current_clue_id is None
    assert state.steal_active is False


def test_correct_steal_scores_without_adding_to_answered_rotation() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST12")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False)
    buzz(state, "Blue Team", "Ben")
    score_current_clue(state, correct=True)

    blue = next(team for team in state.teams if team.name == "Blue Team")
    assert blue.score == clue.value
    assert blue.answered_students == []
    assert blue.correct_counts["Ben"] == 1


def test_reset_buzzer_only_clears_current_buzz_during_steal() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST14")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False)
    buzz(state, "Blue Team", "Ben")

    reset_buzzer(state)

    assert state.steal_active is True
    assert state.steal_lockout_team == "Red Team"
    assert state.buzz_winner is None
    assert state.buzz_winner_student is None
    assert state.buzz_order == []


def test_teacher_can_skip_unanswered_steal_by_selecting_next_clue() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST10")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]

    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    select_clue(state, clues[0].id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False)

    select_clue(state, clues[1].id)

    assert clues[0].revealed is True
    assert state.current_clue_id == clues[1].id
    assert state.steal_active is False
    assert state.steal_lockout_team is None


def test_teacher_can_continue_after_steal_window_without_more_points() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST13")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False)

    continue_current_clue(state)

    scores = {team.name: team.score for team in state.teams}
    assert scores["Red Team"] == -clue.value
    assert scores["Blue Team"] == 0
    assert clue.revealed is True
    assert state.current_clue_id is None
    assert state.steal_active is False


def test_wrong_without_steal_buzz_continues_without_more_points() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST15")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_team(state, "Red Team", "Alice")
    join_team(state, "Blue Team", "Ben")
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False)

    score_current_clue(state, correct=False, team_name="Red Team")

    scores = {team.name: team.score for team in state.teams}
    assert scores["Red Team"] == -clue.value
    assert scores["Blue Team"] == 0
    assert clue.revealed is True
    assert state.current_clue_id is None


def test_teacher_can_override_scores() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST11")

    set_team_scores(state, {"Red Team": 1200, "Blue Team": -100})

    scores = {team.name: team.score for team in state.teams}
    assert scores["Red Team"] == 1200
    assert scores["Blue Team"] == -100
    assert scores["Purple Team"] == 0


def test_teacher_can_remove_stale_student() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST16")
    join_team(state, "Red Team", "Alice")
    join_team(state, "Red Team", "Ari")
    select_clue(
        state,
        next(
            clue.id
            for category in state.categories
            for clue in category.clues
            if not clue.is_daily_double
        ),
    )
    buzz(state, "Red Team", "Alice")

    remove_student(state, "Red Team", "Alice")

    red = next(team for team in state.teams if team.name == "Red Team")
    assert red.students == ["Ari"]
    assert state.buzz_winner is None


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


def test_public_state_hides_final_category_until_wagers_are_locked() -> None:
    state = create_game_state(load_game_config("games/example.yaml"), session_code="TEST18")
    join_team(state, "Red Team", "Alice")
    reveal_final(state)

    payload = public_state(state)

    assert payload["final"]["category"] == "Final Jeopardy"
    assert payload["final"]["prompt"] == ""
    assert "answer" not in payload["final"]


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
