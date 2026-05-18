# Classroom Jeopardy

A LAN-hosted Jeopardy game for classroom use. The teacher runs the FastAPI app, students join from a browser on the same Wi-Fi, and the first buzzer press locks the clue until the teacher scores or resets it.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run

```bash
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Teacher Accounts

Teacher pages and teacher-only actions require login. If no accounts are configured, the default development login is:

```text
teacher / jeopardy
```

To add one or more teacher accounts, set `JEOPARDY_TEACHERS` before starting the app. Use comma-separated `username:password` pairs:

```bash
JEOPARDY_TEACHERS="alice:password1,bob:password2" .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Each teacher account can run its own game session.

Teacher view:

```text
http://localhost:8000/teacher
```

Student join URL:

```text
http://TEACHER_LAN_IP:8000/join
```

On Linux, you can usually find the LAN IP with:

```bash
hostname -I
```

## Editing A Game

Edit `games/example.yaml` or create another YAML file with the same shape. YAML files in `games/` appear in the teacher console game dropdown.

Numeric-looking answers such as `404` can be written with or without quotes.

## How To Run A Game

1. Open `/teacher` and log in.
2. Select a YAML game file from the dropdown.
3. Choose how many teams will play and edit the team names.
4. Press **Start New Game**.
5. Give students the room code and the `/join` URL.
6. Open **Presentation** on the projector or shared display.

Students enter the room code, select their team from a dropdown, enter their name, and join. If a student accidentally leaves and rejoins with the same name, the roster should keep one copy. If a stale or incorrect student entry appears, remove it from the teacher roster.

## Teacher Console

The teacher console is the control room for the game.

- **Start New Game** creates a new room code for the selected YAML file and team setup. Students in the old room are sent back to the join screen.
- **Game dropdown** selects which YAML file to use.
- **Teams** controls how many teams are created for this game, from 2 to 10.
- **Refresh** reloads the current teacher-owned game.
- **Presentation** opens the projector board for the current room.
- **Scores** shows each team score. Edit a score field and press **Save score changes** to manually correct totals.
- **Students** shows joined students grouped by team. Each team group can be collapsed. Use **Remove** to kick stale or incorrect student entries back to the join screen.
- **Team** under Current clue is used for selecting the next clue owner and for Daily Double setup.
- **Correct** scores the active clue for the current buzz winner.
- **Wrong** marks the current buzz winner wrong. On a normal clue, points are deducted and the other teams can immediately buzz for one steal.
- **Continue** ends the current clue without awarding additional points. Use it when no one steals or when you want to move on.
- **Reset buzzers** only clears the current buzz winner and timer. It does not score, reveal, or change steal state.

## Normal Clues

The teacher selects a clue from the board. Students see the clue and may buzz from their own devices. The first buzz locks the clue and starts the answer timer.

If the answer is correct, the team gains the clue value, the clue is marked complete, and the last buzzed team chooses the next clue.

If the answer is wrong, the team loses the clue value and the clue remains open for a steal. The team that answered wrong is locked out of that steal. Students from other teams may buzz. A correct steal earns the clue value. A wrong steal loses no points; the teacher can then press **Continue** to move on.

Student rotation is tracked per team so the same student cannot repeatedly answer for their team before teammates have had a turn. Steal attempts do not count toward that rotation.

## Daily Double

One clue is randomly selected as the Daily Double. When it is chosen, normal buzzing is disabled.

The teacher selects the team, enters a wager, and presses **Lock wager**. The Daily Double question is then shown to that team. The teacher scores it with **Correct** or **Wrong**. Daily Double scoring uses the wager amount instead of the clue value.

## Final Jeopardy

When all board clues are complete, the game moves into Final Jeopardy.

- **Start Final / Reveal Prompt** starts the wager phase, then reveals the final prompt when wagers are ready.
- Student team leaders enter wagers and final answers from their devices.
- **Override Wagers** lets the teacher manually adjust final wagers if needed.
- The teacher marks each team correct or incorrect.
- **Calculate Final Scores** computes the final totals.
- **Reveal Next Place** steps through the final results on the presentation board.

If students do not submit before timers expire, the app advances with default wager or blank answer behavior.

## Presentation Board

Open the **Presentation** link from the teacher console on a projector or shared display. It is tied to the current room code, so each teacher's game can have its own presentation board.

The presentation board shows the scoreboard, categories, clue values, active questions, answer reveals, and Final Jeopardy results. It is display-only; all game control happens from the teacher console.
