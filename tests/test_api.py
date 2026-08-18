import asyncio

from httpx import ASGITransport, AsyncClient

from app import main
from app.store import GameStore


AUTH = ("teacher", "jeopardy")
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


def use_test_store(tmp_path, monkeypatch) -> None:
    test_store = GameStore(tmp_path / "test.db")
    monkeypatch.setattr(main, "store", test_store)


async def join_classroom(client: AsyncClient, session_code: str) -> None:
    for team_name, students in CLASSROOM.items():
        for student in students:
            response = await client.post(
                "/api/join",
                json={
                    "session_code": session_code,
                    "team_name": team_name,
                    "student_name": student,
                },
            )
            assert response.status_code == 200


def test_start_join_select_buzz_score_flow(tmp_path, monkeypatch) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start_response = await client.post(
                "/api/game/start",
                json={"config_path": GAME_PATH, "team_names": TEAM_NAMES},
                auth=AUTH,
            )
            assert start_response.status_code == 200
            state = start_response.json()
            code = state["session_code"]

            await join_classroom(client, code)

            clue = next(
                clue
                for category in state["categories"]
                for clue in category["clues"]
                if not clue["is_daily_double"]
            )
            select_response = await client.post(
                "/api/clue/select",
                json={"session_code": code, "clue_id": clue["id"], "selecting_team": "Red Team"},
                auth=AUTH,
            )
            assert select_response.status_code == 200

            buzz_response = await client.post(
                "/api/buzz",
                json={"session_code": code, "team_name": "Red Team", "student_name": "Alice"},
            )
            assert buzz_response.status_code == 200
            assert buzz_response.json()["buzz_winner"] == "Red Team"
            assert buzz_response.json()["buzz_winner_student"] == "Alice"

            score_response = await client.post(
                "/api/clue/score",
                json={"session_code": code, "correct": True, "team_name": "Red Team"},
                auth=AUTH,
            )
            assert score_response.status_code == 200
            scores = {team["name"]: team["score"] for team in score_response.json()["teams"]}
            assert scores["Red Team"] == clue["value"]

    asyncio.run(scenario())


def test_student_state_hides_answers(tmp_path, monkeypatch) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            state = (
                await client.post(
                    "/api/game/start",
                    json={"config_path": GAME_PATH, "team_names": TEAM_NAMES},
                    auth=AUTH,
                )
            ).json()
            code = state["session_code"]

            student_state = (await client.get(f"/api/game/{code}")).json()
            teacher_state = (await client.get(f"/api/game/{code}?teacher=true", auth=AUTH)).json()

        assert "answer" not in student_state["categories"][0]["clues"][0]
        assert "answer" in teacher_state["categories"][0]["clues"][0]

    asyncio.run(scenario())


def test_manual_score_persists_as_final_wager_limit(tmp_path, monkeypatch) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            state = (
                await client.post(
                    "/api/game/start",
                    json={"config_path": GAME_PATH, "team_names": TEAM_NAMES},
                    auth=AUTH,
                )
            ).json()
            code = state["session_code"]
            await client.post(
                "/api/join",
                json={"session_code": code, "team_name": "Red Team", "student_name": "Alice"},
            )
            await client.post(
                "/api/join",
                json={"session_code": code, "team_name": "Blue Team", "student_name": "Ben"},
            )

            score_response = await client.post(
                "/api/scores",
                json={"session_code": code, "scores": {"Red Team": 1700, "Blue Team": 400}},
                auth=AUTH,
            )
            assert score_response.status_code == 200
            assert main.store.get(code).teams[0].score == 1700

            final_state = (
                await client.post(
                    "/api/final/reveal",
                    json={"session_code": code},
                    auth=AUTH,
                )
            ).json()
            leader = final_state["final"]["leaders"]["Red Team"]
            wager_response = await client.post(
                "/api/final/wager",
                json={
                    "session_code": code,
                    "team_name": "Red Team",
                    "student_name": leader,
                    "wager": 1700,
                },
            )
            assert wager_response.status_code == 200
            assert main.store.get(code).final.wagers["Red Team"] == 1700

    asyncio.run(scenario())


def test_starting_new_game_keeps_previous_session(tmp_path, monkeypatch) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = (
                await client.post(
                    "/api/game/start",
                    json={"config_path": GAME_PATH, "team_names": TEAM_NAMES},
                    auth=AUTH,
                )
            ).json()
            old_code = first["session_code"]

            reset_response = await client.post(
                "/api/game/start",
                json={
                    "config_path": GAME_PATH,
                    "team_names": ["Green Team", "Gold Team"],
                    "previous_session_code": old_code,
                },
                auth=AUTH,
            )
            assert reset_response.status_code == 200
            assert reset_response.json()["session_code"] != old_code

            old_response = await client.get(f"/api/game/{old_code}")
            assert old_response.status_code == 404

    asyncio.run(scenario())


def test_browser_join_cleans_other_session_and_leave_is_idempotent(
    tmp_path,
    monkeypatch,
) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = (
                await client.post(
                    "/api/game/start",
                    json={"config_path": GAME_PATH, "team_names": TEAM_NAMES},
                    auth=AUTH,
                )
            ).json()
            second = (
                await client.post(
                    "/api/game/start",
                    json={"config_path": GAME_PATH, "team_names": TEAM_NAMES},
                    auth=AUTH,
                )
            ).json()
            participant_id = "browser-1"
            first_join = await client.post(
                "/api/join",
                json={
                    "session_code": first["session_code"],
                    "team_name": "Red Team",
                    "student_name": "Alice",
                    "participant_id": participant_id,
                },
            )
            assert first_join.status_code == 200

            second_join = await client.post(
                "/api/join",
                json={
                    "session_code": second["session_code"],
                    "team_name": "Blue Team",
                    "student_name": "Alice",
                    "participant_id": participant_id,
                },
            )
            assert second_join.status_code == 200
            assert main.store.get(first["session_code"]).teams[0].students == []
            assert main.store.get(second["session_code"]).teams[1].students == ["Alice"]

            stale_leave = await client.post(
                "/api/leave",
                json={
                    "session_code": first["session_code"],
                    "team_name": "Red Team",
                    "student_name": "Alice",
                    "participant_id": participant_id,
                },
            )
            assert stale_leave.status_code == 200
            assert main.store.get(second["session_code"]).teams[1].students == ["Alice"]

            current_leave = await client.post(
                "/api/leave",
                json={
                    "session_code": second["session_code"],
                    "team_name": "Blue Team",
                    "student_name": "Alice",
                    "participant_id": participant_id,
                },
            )
            assert current_leave.status_code == 200
            assert main.store.get(second["session_code"]).teams[1].students == []

            legacy_join = await client.post(
                "/api/join",
                json={
                    "session_code": first["session_code"],
                    "team_name": "Red Team",
                    "student_name": "Legacy Alice",
                },
            )
            assert legacy_join.status_code == 200
            legacy_leave = await client.post(
                "/api/leave",
                json={
                    "session_code": first["session_code"],
                    "team_name": "Red Team",
                    "student_name": "Legacy Alice",
                    "participant_id": "new-browser-id",
                },
            )
            assert legacy_leave.status_code == 200
            assert main.store.get(first["session_code"]).teams[0].students == []

    asyncio.run(scenario())
