let sessionCode = localStorage.getItem("jeopardy_session") || "";
let teamName = localStorage.getItem("jeopardy_team") || "";
let socket = null;
let state = null;

const $ = (id) => document.getElementById(id);

function showBuzzer() {
  $("join-card").classList.add("hidden");
  $("buzzer-card").classList.remove("hidden");
  $("student-session").textContent = `Session ${sessionCode}`;
  $("student-team").textContent = teamName;
}

function connectSocket() {
  if (socket) {
    socket.close();
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws/game/${sessionCode}`);
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "state") {
      state = message.state;
      render();
    }
  };
}

function currentClue() {
  if (!state?.current_clue_id) {
    return null;
  }
  for (const category of state.categories) {
    for (const clue of category.clues) {
      if (clue.id === state.current_clue_id) {
        return clue;
      }
    }
  }
  return null;
}

function render() {
  if (!state) {
    return;
  }
  const clue = currentClue();
  const button = $("buzz-button");
  if (!clue) {
    $("student-clue").textContent = state.phase === "final"
      ? `${state.final.category}: ${state.final.revealed ? state.final.prompt : "Waiting for Final Jeopardy."}`
      : "Waiting for the teacher to select a clue.";
    button.disabled = true;
  } else if (clue.is_daily_double) {
    $("student-clue").textContent = "Daily Double. The teacher will manage the wager.";
    button.disabled = true;
  } else {
    $("student-clue").textContent = clue.prompt;
    button.disabled = Boolean(state.buzz_winner);
  }

  if (state.buzz_winner === teamName) {
    $("student-status").textContent = "You buzzed first.";
  } else if (state.buzz_winner) {
    $("student-status").textContent = `${state.buzz_winner} buzzed first.`;
  } else if (clue && !clue.is_daily_double) {
    $("student-status").textContent = "Buzz when ready.";
  } else {
    $("student-status").textContent = "";
  }

  const scores = $("student-scores");
  scores.innerHTML = "";
  state.teams.forEach((team) => {
    const row = document.createElement("div");
    row.className = "score-row";
    row.innerHTML = `<span>${team.name}</span><strong>$${team.score}</strong>`;
    scores.appendChild(row);
  });
}

$("join-code").value = sessionCode;
$("team-name").value = teamName;

$("join-game").addEventListener("click", async () => {
  sessionCode = $("join-code").value.trim().toUpperCase();
  teamName = $("team-name").value.trim();
  $("join-error").textContent = "";
  const response = await fetch("/api/join", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_code: sessionCode, team_name: teamName }),
  });
  const data = await response.json();
  if (!response.ok) {
    $("join-error").textContent = data.detail || "Unable to join.";
    return;
  }
  localStorage.setItem("jeopardy_session", sessionCode);
  localStorage.setItem("jeopardy_team", teamName);
  state = data.state;
  showBuzzer();
  render();
  connectSocket();
});

$("buzz-button").addEventListener("click", async () => {
  $("buzz-button").disabled = true;
  const response = await fetch("/api/buzz", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_code: sessionCode, team_name: teamName }),
  });
  if (!response.ok) {
    const data = await response.json();
    $("student-status").textContent = data.detail || "Buzz did not register.";
    $("buzz-button").disabled = false;
  }
});

if (sessionCode && teamName) {
  showBuzzer();
  connectSocket();
}
