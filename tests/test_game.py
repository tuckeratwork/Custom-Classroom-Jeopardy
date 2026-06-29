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
    lock_daily_double_wager,
    remove_student,
    random_buzz_eligible,
    reset_final,
    reset_buzzer,
    reset_buzzer_rotation,
    reveal_final,
    score_current_clue,
    score_final,
    select_clue,
    set_auto_random,
    set_reactions,
    set_team_scores,
    set_final_wagers,
    submit_daily_double_answer,
    submit_daily_double_wager,
    submit_final_answer,
    submit_final_wager,
    advance_final_reveal,
    public_state,
    validate_reaction,
)

GAME_PATH = "games/cna_modules_6_9.yaml"
TEAM_NAMES = ["Red Team", "Blue Team"]
CLASSROOM = {
    "Red Team": [
        "Alice",
        "Ari",
        "Riley",
        "Rowan",
        "Reese",
        "Rory",
        "River",
        "Remy",
        "Robin",
        "Ryan",
    ],
    "Blue Team": [
        "Ben",
        "Bea",
        "Blake",
        "Bailey",
        "Briar",
        "Brett",
        "Brooke",
        "Bryce",
        "Billie",
        "Brooklyn",
    ],
}


def make_state(session_code: str):
    return create_game_state(load_game_config(GAME_PATH), session_code=session_code)


def join_classroom(state) -> None:
    for team_name, students in CLASSROOM.items():
        for student in students:
            join_team(state, team_name, student)


def test_load_game_config() -> None:
    config = load_game_config(GAME_PATH)
    assert config.title == "CNA Modules 6 - 9"
    assert len(config.teams) == 2
    assert config.final.category
    assert config.final.prompt
    assert config.final.answer


def test_loads_cna_modules_1_5_config() -> None:
    config = load_game_config("games/cna_modules_1_5.yaml")
    assert config.title == "The REAL CNA Modules 1 - 5"
    assert len(config.categories) == 5


def test_loads_image_test_config() -> None:
    config = load_game_config("games/image_test.yaml")
    assert config.title == "Image Test Jeopardy"
    assert config.categories[0].clues[0].image == "test_images/network_map.svg"


def test_clue_image_config_is_copied_to_state(tmp_path: Path) -> None:
    game_yaml = tmp_path / "image-game.yaml"
    game_yaml.write_text(
        """
title: Image Game
teams:
  - Red Team
  - Blue Team
categories:
  - name: Tools
    clues:
      - value: 100
        prompt: Identify this tool.
        image: module/tool.png
        answer: What is Wireshark?
final:
  category: Final
  prompt: Final prompt
  answer: Final answer
""",
        encoding="utf-8",
    )

    state = create_game_state(load_game_config(game_yaml), session_code="IMG")

    clue = state.categories[0].clues[0]
    assert clue.image == "module/tool.png"


def test_game_state_can_use_runtime_team_names() -> None:
    state = create_game_state(
        load_game_config(GAME_PATH),
        session_code="TEAMS",
        team_names=["Alpha", "Beta", "Gamma"],
        owner_name="teacher-a",
    )

    assert state.owner_name == "teacher-a"
    assert [team.name for team in state.teams] == ["Alpha", "Beta", "Gamma"]


def test_regular_scoring_and_buzzer_lockout() -> None:
    state = make_state("TEST1")
    clue = next(clue for category in state.categories for clue in category.clues if not clue.is_daily_double)

    join_classroom(state)
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
    assert state.last_completed_clue_id == clue.id
    assert state.buzz_expires_at is None
    assert state.last_buzz_team == "Red Team"
    assert state.last_buzz_student == "Alice"


def test_daily_double_requires_wager() -> None:
    state = make_state("TEST2")
    join_classroom(state)
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


def test_daily_double_leader_locks_wager_and_submits_answer() -> None:
    state = make_state("TEST21")
    regular = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )

    join_classroom(state)
    select_clue(state, regular.id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=True)

    select_clue(state, daily.id, selecting_team="Blue Team")

    assert state.daily_double.team_name == "Red Team"
    assert state.daily_double.leader == "Alice"

    submit_daily_double_wager(state, "Red Team", "Alice", 300)
    assert state.daily_double.wager == 300
    assert state.daily_double.question_revealed is True
    assert state.daily_double.answer_expires_at is not None

    submit_daily_double_answer(state, "Red Team", "Alice", "A test answer")
    assert state.daily_double.submission == "A test answer"
    assert state.daily_double.submitted_by == "Alice"


def test_daily_double_wager_uses_500_floor_for_low_scores() -> None:
    state = make_state("TEST31")
    join_classroom(state)
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )
    state.last_buzz_team = "Red Team"
    state.last_buzz_student = "Alice"
    select_clue(state, daily.id)

    with pytest.raises(GameError):
        submit_daily_double_wager(state, "Red Team", "Alice", 501)

    submit_daily_double_wager(state, "Red Team", "Alice", 500)
    assert state.daily_double.wager == 500


def test_daily_double_wager_cannot_exceed_team_score_above_500() -> None:
    state = make_state("TEST32")
    join_classroom(state)
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )
    red = next(team for team in state.teams if team.name == "Red Team")
    red.score = 700
    state.last_buzz_team = "Red Team"
    state.last_buzz_student = "Alice"
    select_clue(state, daily.id)

    with pytest.raises(GameError):
        submit_daily_double_wager(state, "Red Team", "Alice", 701)

    submit_daily_double_wager(state, "Red Team", "Alice", 700)
    assert state.daily_double.wager == 700


def test_daily_double_scoring_uses_locked_student_wager_over_teacher_form_value() -> None:
    state = make_state("TEST33")
    join_classroom(state)
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )
    state.last_buzz_team = "Red Team"
    state.last_buzz_student = "Alice"
    select_clue(state, daily.id)
    submit_daily_double_wager(state, "Red Team", "Alice", 300)

    score_current_clue(state, correct=True, team_name="Red Team", wager=0)

    red = next(team for team in state.teams if team.name == "Red Team")
    assert red.score == 300
    assert state.score_event is not None
    assert state.score_event.delta == 300


def test_teacher_daily_double_wager_uses_same_limit() -> None:
    state = make_state("TEST34")
    join_classroom(state)
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )
    select_clue(state, daily.id, selecting_team="Blue Team")

    with pytest.raises(GameError):
        lock_daily_double_wager(state, "Blue Team", 501)

    lock_daily_double_wager(state, "Blue Team", 500)
    assert state.daily_double.wager == 500


def test_daily_double_rejects_non_leader_submission() -> None:
    state = make_state("TEST22")
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )

    join_classroom(state)
    state.last_buzz_team = "Red Team"
    state.last_buzz_student = "Alice"
    select_clue(state, daily.id)

    with pytest.raises(GameError):
        submit_daily_double_wager(state, "Red Team", "Ari", 200)


def test_daily_double_config_overwrites_random_clue_content() -> None:
    state = make_state("TEST8")
    clue = next(clue for category in state.categories for clue in category.clues if clue.is_daily_double)

    assert clue.prompt.startswith("What was the REAL name")
    assert clue.answer == "Who is John Kramer?"


def test_student_buzzer_rotation_resets_after_team_cycle() -> None:
    state = make_state("TEST4")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]

    join_classroom(state)

    for index, student in enumerate(CLASSROOM["Red Team"]):
        select_clue(state, clues[index].id)
        buzz(state, "Red Team", student)
        score_current_clue(state, correct=False, team_name="Red Team")

    select_clue(state, clues[len(CLASSROOM["Red Team"])].id)
    buzz(state, "Red Team", "Alice")
    assert state.buzz_winner_student == "Alice"


def test_teacher_can_clear_rotation_and_randomly_buzz_remaining_student(monkeypatch) -> None:
    state = make_state("TEST17")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]
    join_classroom(state)
    select_clue(state, clues[0].id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=True)
    select_clue(state, clues[1].id)
    monkeypatch.setattr("app.game.random.choice", lambda choices: choices[0])

    random_buzz_eligible(state)
    assert state.buzz_winner == "Red Team"
    assert state.buzz_winner_student == "Ari"

    reset_buzzer_rotation(state)
    assert next(team for team in state.teams if team.name == "Red Team").answered_students == []


def test_auto_random_starts_on_selected_clue_and_picks_if_no_buzz(monkeypatch) -> None:
    state = make_state("TEST19")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    join_classroom(state)
    monkeypatch.setattr("app.game.random.choice", lambda choices: choices[0])
    set_auto_random(state, True)

    select_clue(state, clue.id)
    assert state.auto_random_expires_at is not None
    state.auto_random_expires_at = 1
    advance_auto_random(state)

    assert state.buzz_winner == "Red Team"
    assert state.buzz_winner_student == "Alice"
    assert state.auto_random_expires_at is None


def test_auto_random_timer_clears_when_student_buzzes() -> None:
    state = make_state("TEST20")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    join_classroom(state)
    set_auto_random(state, True)
    select_clue(state, clue.id)

    buzz(state, "Red Team", "Alice")

    assert state.auto_random_expires_at is None


def test_manual_buzz_gets_ten_seconds_and_random_buzz_gets_fifteen(monkeypatch) -> None:
    state = make_state("TEST27")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]
    join_classroom(state)
    monkeypatch.setattr("app.game.time.time", lambda: 1000)
    monkeypatch.setattr("app.game.random.choice", lambda choices: choices[0])

    select_clue(state, clues[0].id)
    buzz(state, "Red Team", "Alice")
    assert state.buzz_expires_at == 1010
    score_current_clue(state, correct=True)

    select_clue(state, clues[1].id)
    random_buzz_eligible(state)
    assert state.buzz_expires_at == 1015


def test_reactions_validate_support_and_opposition() -> None:
    state = make_state("TEST25")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    join_classroom(state)
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")

    assert validate_reaction(state, "Red Team", "Ari", "👏") == "support"
    assert validate_reaction(state, "Blue Team", "Ben", "🍅") == "opposition"
    with pytest.raises(GameError):
        validate_reaction(state, "Red Team", "Alice", "👏")
    with pytest.raises(GameError):
        validate_reaction(state, "Red Team", "Ari", "🍅")
    with pytest.raises(GameError):
        validate_reaction(state, "Blue Team", "Ben", "👏")


def test_reactions_can_be_disabled_and_skip_daily_double() -> None:
    state = make_state("TEST26")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    daily = next(
        clue
        for category in state.categories
        for clue in category.clues
        if clue.is_daily_double
    )
    join_classroom(state)
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    set_reactions(state, False)

    with pytest.raises(GameError):
        validate_reaction(state, "Red Team", "Ari", "👏")

    set_reactions(state, True)
    score_current_clue(state, correct=True)
    select_clue(state, daily.id)
    with pytest.raises(GameError):
        validate_reaction(state, "Red Team", "Ari", "👏")


def test_wrong_answer_can_open_one_steal_without_rotation_or_penalty() -> None:
    state = make_state("TEST9")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]

    join_classroom(state)

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
    state = make_state("TEST12")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_classroom(state)
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
    state = make_state("TEST14")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_classroom(state)
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
    state = make_state("TEST10")
    clues = [
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    ]

    join_classroom(state)
    select_clue(state, clues[0].id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=False)

    select_clue(state, clues[1].id)

    assert clues[0].revealed is True
    assert state.current_clue_id == clues[1].id
    assert state.last_completed_clue_id is None
    assert state.steal_active is False
    assert state.steal_lockout_team is None


def test_teacher_can_continue_after_steal_window_without_more_points() -> None:
    state = make_state("TEST13")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_classroom(state)
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
    assert state.last_completed_clue_id == clue.id


def test_wrong_without_steal_buzz_continues_without_more_points() -> None:
    state = make_state("TEST15")
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )

    join_classroom(state)
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
    state = make_state("TEST11")
    join_classroom(state)

    set_team_scores(state, {"Red Team": 1200, "Blue Team": -100})

    scores = {team.name: team.score for team in state.teams}
    assert scores["Red Team"] == 1200
    assert scores["Blue Team"] == -100


def test_teacher_can_remove_stale_student() -> None:
    state = make_state("TEST16")
    join_classroom(state)
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
    assert "Alice" not in red.students
    assert len(red.students) == 9
    assert state.buzz_winner is None


def test_final_uses_joined_student_teams_only() -> None:
    state = make_state("TEST5")
    join_classroom(state)
    reveal_final(state)

    assert state.phase == "final_wager"
    assert set(state.final.leaders) == {"Red Team", "Blue Team"}

    state.final.wager_expires_at = 0
    advance_final_timers(state)
    assert state.phase == "final_answer"
    assert state.final.wagers == {"Red Team": 500, "Blue Team": 500}


def test_final_manual_wager_override_works_without_joined_students() -> None:
    state = make_state("TEST23")

    reveal_final(state)
    set_final_wagers(state, {"Red Team": 300, "Blue Team": 700})

    assert state.phase == "final_answer"
    assert state.final.phase == "answer"
    assert state.final.wagers == {"Red Team": 300, "Blue Team": 700}


def test_starting_final_clears_last_completed_clue() -> None:
    state = make_state("TEST24")
    join_classroom(state)
    clue = next(
        clue
        for category in state.categories
        for clue in category.clues
        if not clue.is_daily_double
    )
    select_clue(state, clue.id)
    buzz(state, "Red Team", "Alice")
    score_current_clue(state, correct=True)
    assert state.last_completed_clue_id == clue.id

    reveal_final(state)

    assert state.last_completed_clue_id is None


def test_public_state_hides_final_category_until_wagers_are_locked() -> None:
    state = make_state("TEST18")
    join_classroom(state)
    reveal_final(state)

    payload = public_state(state)

    assert payload["final"]["category"] == "Final Jeopardy"
    assert payload["final"]["prompt"] == ""
    assert "answer" not in payload["final"]


def test_final_wager_allows_500_floor_for_low_scores() -> None:
    state = make_state("TEST6")
    join_classroom(state)
    red = next(team for team in state.teams if team.name == "Red Team")
    red.score = -200
    reveal_final(state)
    state.final.leaders["Red Team"] = "Alice"

    submit_final_wager(state, "Red Team", "Alice", 500)
    assert state.final.wagers["Red Team"] == 500

    state = make_state("TEST7")
    join_classroom(state)
    reveal_final(state)
    state.final.leaders["Red Team"] = "Alice"
    with pytest.raises(GameError):
        submit_final_wager(state, "Red Team", "Alice", 501)


def test_final_jeopardy_scoring_once() -> None:
    state = make_state("TEST3")
    join_classroom(state)
    reveal_final(state)
    assert state.phase == "final_wager"
    state.final.leaders["Red Team"] = "Alice"
    state.final.leaders["Blue Team"] = "Ben"
    submit_final_wager(state, "Red Team", "Alice", 500)
    submit_final_wager(state, "Blue Team", "Ben", 200)
    assert state.phase == "final_answer"
    submit_final_answer(state, "Red Team", "Alice", "A correct response")
    submit_final_answer(state, "Blue Team", "Ben", "An incorrect response")
    assert state.phase == "final_review"
    score_final(state, {"Red Team": True, "Blue Team": False})
    calculate_final_winners(state)
    calculate_final_winners(state)

    scores = {team.name: team.score for team in state.teams}
    assert scores == {"Red Team": 0, "Blue Team": 0}
    assert state.final.final_scores == {"Red Team": 500, "Blue Team": -200}
    assert state.final.reveal_order == ["Blue Team", "Red Team"]
    advance_final_reveal(state)
    assert state.final.reveal_spinning_index == 0
    assert state.final.reveal_step == 0
    advance_final_reveal(state)
    assert state.final.reveal_spinning_index is None
    assert state.final.reveal_step == 1
    assert state.phase == "complete"


def test_final_cannot_score_or_calculate_before_review() -> None:
    state = make_state("TEST35")
    join_classroom(state)
    reveal_final(state)

    with pytest.raises(GameError):
        score_final(state, {"Red Team": True, "Blue Team": False})

    with pytest.raises(GameError):
        calculate_final_winners(state)


def test_reset_final_clears_wagers_submissions_and_results() -> None:
    state = make_state("TEST36")
    join_classroom(state)
    reveal_final(state)
    state.final.leaders["Red Team"] = "Alice"
    state.final.leaders["Blue Team"] = "Ben"
    submit_final_wager(state, "Red Team", "Alice", 500)
    submit_final_wager(state, "Blue Team", "Ben", 200)
    submit_final_answer(state, "Red Team", "Alice", "A correct response")
    submit_final_answer(state, "Blue Team", "Ben", "An incorrect response")
    score_final(state, {"Red Team": True, "Blue Team": False})
    calculate_final_winners(state)

    reset_final(state)

    assert state.phase == "final_wager"
    assert state.final.phase == "wager"
    assert state.final.wagers == {}
    assert state.final.submissions == {}
    assert state.final.scored == {}
    assert state.final.final_scores == {}
    assert state.final.results_revealed is False


def test_invalid_yaml_reports_error(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("title: Missing pieces\n", encoding="utf-8")

    with pytest.raises(GameError):
        load_game_config(bad_yaml)


def test_malformed_yaml_reports_game_error(tmp_path: Path) -> None:
    bad_yaml = tmp_path / "bad-syntax.yaml"
    bad_yaml.write_text(
        "title: Bad\ncategories:\n  - name: Broken\n    clues:\n      - prompt: Text: breaks\n",
        encoding="utf-8",
    )

    with pytest.raises(GameError, match="could not parse game YAML"):
        load_game_config(bad_yaml)
