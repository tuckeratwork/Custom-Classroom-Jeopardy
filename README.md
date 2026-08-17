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

Edit one of the YAML files in `games/` or create another YAML file with the same shape. YAML files directly inside `games/` appear in the teacher console game dropdown.

Numeric-looking answers such as `404` can be written with or without quotes.

## Question Images

Normal clues can include an image. Images appear on both the presentation board and the student buzzer screen when that clue is active.

Put local image files under `games/assets/`, then reference them from the clue with a path relative to that folder:

```yaml
categories:
  - name: Picture This
    clues:
      - value: 100
        prompt: This diagram shows traffic moving between clients and servers.
        image: test_images/network_map.svg
        answer: What is a network map?
```

For that example, the file would live at:

```text
games/assets/test_images/network_map.svg
```

You can use common browser-supported image types such as `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, or `.svg`. You can also use a full image URL, but local files are usually more reliable for a classroom LAN game.

Daily Double clues are generated from the `daily_double:` section and currently do not use clue images. If the randomly selected Daily Double was originally a clue with an image, that image is hidden for the Daily Double.

## How To Run A Game

1. Open `/teacher` and log in.
2. Select a YAML game file from the dropdown.
3. Choose how many teams will play and edit the team names.
4. Press **Start New Game**.
5. Give students the room code and the `/join` URL.
6. Open **Presentation** on the projector or shared display.

Students enter the room code, select their team from a dropdown, enter their name, and join. If a student accidentally leaves and rejoins with the same name, the roster should keep one copy. If a student joins the wrong team, use the destination selector and **Move** button in the teacher roster. The student's buzzer updates automatically. Students cannot be moved during a Daily Double or Final Jeopardy. If a stale or incorrect student entry appears, use **Remove** to send that student back to the join screen.

## Teacher Console

The teacher console is the control room for the game.

- **Start New Game** creates a new room code for the selected YAML file and team setup. Students in the old room are sent back to the join screen.
- **Game dropdown** selects which YAML file to use.
- **Teams** controls how many teams are created for this game, from 2 to 10.
- **Refresh** reloads the current teacher-owned game.
- **Presentation** opens the projector board for the current room.
- **Scores** shows each team score. Edit a score field and press **Save score changes** to manually correct totals. A confirmation appears after the backend saves the new totals, and the saved score is used for Daily Double and Final Jeopardy wager limits.
- **Students** shows joined students grouped by team. Each team group can be collapsed. Use **Move** to correct a student's team or **Remove** to kick a stale or incorrect entry back to the join screen.
- **Team** under Current clue is used for selecting the next clue owner and for Daily Double setup.
- **Correct** scores the active clue for the current buzz winner.
- **Wrong** marks the current buzz winner wrong. On a normal clue, points are deducted and the other teams can immediately buzz for one steal.
- **Continue** ends the current clue without awarding additional points. Use it when no one steals or when you want to move on.
- **Reset buzzers** only clears the current buzz winner and timer. It does not score, reveal, or change steal state.
- **Clear student rotation** lets all joined students buzz again if the rotation creates a stalemate.
- **Random eligible buzz** randomly selects one currently eligible student and buzzes in for them.
- **Auto-random eligible student after 15 seconds** starts a countdown when a normal clue is selected. If no student buzzes before it reaches zero, the app randomly buzzes one eligible student.
- **Early buzz penalty with 5-second opening countdown** leaves buzzers clickable while students see **Buzzers opening in...**. A student who buzzes during those five seconds is locked out for that clue without using their normal rotation turn. Early penalties do not apply to steals. When auto-random is also enabled, both timers begin together and auto-random still occurs 15 seconds after clue selection. If only one eligible student remains, that student is selected automatically with a 20-second answer timer instead of allowing early penalties to leave the clue with no possible buzzer. A student whose early buzz triggers this selection receives a separate penalty explanation naming the automatically selected student.
- **All Participants Required for Buzzer Eligibility Reset** changes the normal rotation so it resets only after every connected student across every team has answered. **Clear student rotation** remains a teacher override and immediately resets everyone.
- **Show eligible students** toggles the eligible student list. It is off by default to keep the teacher panel less cluttered.
- **Eligible to buzz** shows which students can currently buzz based on the team rotation and steal lockout rules when that display is enabled.
- **Emoji reactions** lets students send anonymous encouragement or playful opposition emojis while a normal clue has an active buzz winner. The answering student and presentation board see the emoji streams. Reactions are disabled for Daily Double and Final Jeopardy.
- **Reset Final Jeopardy** restarts Final Jeopardy if something goes wrong. The teacher must confirm this action, and students will need to lock wagers and submit answers again.
- **Calculate Final Scores** is only available after Final Jeopardy reaches review mode, meaning wagers are locked and answers have been submitted or timed out.
- **Reveal Next Place** steps through the final results after scores have been calculated.

## Normal Clues

The teacher selects a clue from the board. Students see the clue and may buzz from their own devices. The selected tile expands into the active question on the presentation board. The first buzz locks the clue and starts the answer timer.

Manual buzzes get a 10 second answer timer. Randomly selected students get 15 seconds. The active student's buzzer screen changes color near the end of the timer: yellow for the final two seconds and red once time expires.

If the answer is correct, the team gains the clue value, the clue is marked complete, and the last buzzed team chooses the next clue.

If the answer is wrong, the team loses the clue value and the clue remains open for one steal. The team that answered wrong is locked out of that steal. Students from other teams may buzz. A correct steal earns the clue value. A wrong steal loses no points; the teacher can then press **Continue** to move on.

Student rotation is tracked per team by default so the same student cannot repeatedly answer for their team before teammates have had a turn. The optional all-participants setting makes this a single classroom-wide cycle instead. Steal attempts and early buzz penalties do not count toward either rotation. When three or fewer students remain eligible on a team, each student on that team sees their names; students never see another team's remaining list.

The correct answer is shown after **Correct** or **Continue** and stays visible on the presentation board until the next clue is selected.

## Daily Double

One clue is randomly selected as the Daily Double. When it is chosen, normal buzzing is disabled.

The Daily Double leader is the student who buzzed in last, since that student chose the next tile. That leader locks the team's wager from their student screen, then receives the Daily Double prompt and submits the team's answer before the timer expires. The teacher reviews the submitted answer in the Current clue panel and scores it with **Correct** or **Wrong**.

Daily Double wagers follow the same limit as Final Jeopardy: a team may wager up to its current score, or up to `$500` if the team has less than `$500`. The student screen tells the leader when the team is below `$500`.

The teacher can still enter a wager and press **Lock wager** from the console if a manual override is needed. Daily Double scoring uses the locked wager amount instead of the clue value.

## Final Jeopardy

When all board clues are complete, the game moves into Final Jeopardy.

- Before wagers are locked, the final category, prompt, and answer stay hidden.
- Student team leaders enter wagers from their devices. A team may wager up to its current score, or up to `$500` if the team has less than `$500`.
- Once wagers are locked, the final category and prompt are revealed.
- Team leaders submit final answers from their devices.
- The teacher reviews each submission and marks each team correct or incorrect.
- **Calculate Final Scores** computes the final totals after all wagers and answers are ready for review.
- **Reveal Next Place** steps through the final results on the presentation board.
- **Reset Final Jeopardy** restarts the Final Jeopardy wager phase after a confirmation popup.

If students do not submit before timers expire, the app advances with default wager or blank answer behavior.

## Presentation Board

Open the **Presentation** link from the teacher console on a projector or shared display. It is tied to the current room code, so each teacher's game can have its own presentation board.

The presentation board shows the scoreboard, categories, clue values, active questions, question images, answer reveals, buzz winners, emoji reactions, auto-random countdowns, and Final Jeopardy results. The team scores and previous-answer panel sit below the full-width clue board. Use the **-**, percentage, and **+** controls beside the scoreboard to decrease, reset, or increase the presentation size without changing the teacher console. The selected size is remembered by that presentation browser. The board is otherwise display-only; all game control happens from the teacher console.
