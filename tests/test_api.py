import asyncio

from httpx import ASGITransport, AsyncClient

from app import main
from app.store import GameStore


def use_test_store(tmp_path, monkeypatch) -> None:
    test_store = GameStore(tmp_path / "test.db")
    monkeypatch.setattr(main, "store", test_store)


def test_start_join_select_buzz_score_flow(tmp_path, monkeypatch) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            start_response = await client.post(
                "/api/game/start",
                json={"config_path": "games/example.yaml"},
            )
            assert start_response.status_code == 200
            state = start_response.json()
            code = state["session_code"]

            join_response = await client.post(
                "/api/join",
                json={"session_code": code, "team_name": "Red Team", "student_name": "Alice"},
            )
            assert join_response.status_code == 200

            clue = next(
                clue
                for category in state["categories"]
                for clue in category["clues"]
                if not clue["is_daily_double"]
            )
            select_response = await client.post(
                "/api/clue/select",
                json={"session_code": code, "clue_id": clue["id"], "selecting_team": "Red Team"},
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
                await client.post("/api/game/start", json={"config_path": "games/example.yaml"})
            ).json()
            code = state["session_code"]

            student_state = (await client.get(f"/api/game/{code}")).json()
            teacher_state = (await client.get(f"/api/game/{code}?teacher=true")).json()

        assert "answer" not in student_state["categories"][0]["clues"][0]
        assert "answer" in teacher_state["categories"][0]["clues"][0]

    asyncio.run(scenario())


def test_reset_invalidates_previous_session(tmp_path, monkeypatch) -> None:
    use_test_store(tmp_path, monkeypatch)

    async def scenario() -> None:
        transport = ASGITransport(app=main.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = (
                await client.post("/api/game/start", json={"config_path": "games/example.yaml"})
            ).json()
            old_code = first["session_code"]

            reset_response = await client.post(
                "/api/game/start",
                json={"config_path": "games/example.yaml", "previous_session_code": old_code},
            )
            assert reset_response.status_code == 200
            assert reset_response.json()["session_code"] != old_code

            old_response = await client.get(f"/api/game/{old_code}")
            assert old_response.status_code == 404

    asyncio.run(scenario())
