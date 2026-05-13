let state = null;
let socket = null;

const $ = (id) => document.getElementById(id);

function sessionCode() {
  return state?.session_code || $("session-code").textContent.trim();
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Request failed");
  }
  state = data.state || data;
  render();
  return data;
}

async function fetchTeacherState(code) {
  const response = await fetch(`/api/game/${code}?teacher=true`);
  if (!response.ok) {
    return;
  }
  state = await response.json();
  render();
}

function connectSocket(code) {
  if (socket) {
    socket.close();
  }
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${scheme}://${location.host}/ws/game/${code}`);
  socket.onmessage = () => fetchTeacherState(code);
}

function render() {
  if (!state) {
    return;
  }
  $("game-title").textContent = state.title;
  $("session-code").textContent = state.session_code;
  renderBoard();
  renderScores();
  renderControls();
  renderFinal();
}

function renderBoard() {
  const board = $("board");
  const columns = state.categories.length;
  board.style.gridTemplateColumns = `repeat(${columns}, minmax(140px, 1fr))`;
  board.innerHTML = "";

  state.categories.forEach((category) => {
    const header = document.createElement("div");
    header.className = "category-header";
    header.textContent = category.name;
    board.appendChild(header);
  });

  const maxRows = Math.max(...state.categories.map((category) => category.clues.length));
  for (let row = 0; row < maxRows; row += 1) {
    state.categories.forEach((category) => {
      const clue = category.clues[row];
      const button = document.createElement("button");
      button.className = "clue-button";
      if (!clue) {
        button.disabled = true;
        button.textContent = "";
      } else {
        button.textContent = clue.revealed ? "" : `$${clue.value}`;
        button.disabled = clue.revealed;
        button.classList.toggle("revealed", clue.revealed);
        button.classList.toggle("active", state.current_clue_id === clue.id);
        button.addEventListener("click", () => selectClue(clue.id));
      }
      board.appendChild(button);
    });
  }
}

function renderScores() {
  const scores = $("scores");
  scores.innerHTML = "";
  const select = $("score-team");
  const previous = select.value;
  select.innerHTML = "";

  state.teams.forEach((team) => {
    const row = document.createElement("div");
    row.className = "score-row";
    row.innerHTML = `<span>${team.name}${team.joined ? "" : " (not joined)"}</span><strong>$${team.score}</strong>`;
    scores.appendChild(row);

    const option = document.createElement("option");
    option.value = team.name;
    option.textContent = team.name;
    select.appendChild(option);
  });

  if (state.buzz_winner) {
    select.value = state.buzz_winner;
  } else if (previous) {
    select.value = previous;
  }
}

function findCurrentClue() {
  for (const category of state.categories) {
    for (const clue of category.clues) {
      if (clue.id === state.current_clue_id) {
        return clue;
      }
    }
  }
  return null;
}

function renderControls() {
  const clue = findCurrentClue();
  const current = $("current-clue");
  const dailyControls = $("daily-double-controls");
  if (!clue) {
    current.classList.add("muted");
    current.textContent = "No clue selected.";
    $("buzz-status").textContent = "";
  } else {
    current.classList.remove("muted");
    const marker = clue.is_daily_double ? "Daily Double: " : "";
    current.innerHTML = `<strong>$${clue.value}</strong><br>${marker}${clue.prompt}<hr><span class="muted">Answer: ${clue.answer}</span>`;
    $("buzz-status").textContent = clue.is_daily_double
      ? `Daily Double for ${state.daily_double.team_name || "selected team"}`
      : state.buzz_winner
        ? `${state.buzz_winner} buzzed first`
        : "Waiting for a buzz.";
  }
  dailyControls.classList.toggle("hidden", !clue?.is_daily_double);
}

function renderFinal() {
  const finalCard = $("final-card");
  finalCard.innerHTML = `<strong>${state.final.category}</strong><br>${state.final.revealed ? state.final.prompt : "Final prompt hidden."}<hr><span class="muted">Answer: ${state.final.answer || ""}</span>`;

  const wagers = $("final-wagers");
  wagers.innerHTML = "";
  state.teams.forEach((team) => {
    const row = document.createElement("div");
    row.className = "final-wager-row";
    const wager = state.final.wagers[team.name] ?? 0;
    const scored = state.final.scored[team.name];
    row.innerHTML = `
      <span>${team.name}</span>
      <input data-wager-team="${team.name}" type="number" min="0" value="${wager}">
      <label><input data-final-team="${team.name}" type="checkbox" ${scored === true ? "checked" : ""}> Correct</label>
    `;
    wagers.appendChild(row);
  });
}

async function selectClue(clueId) {
  const selectingTeam = $("score-team").value || null;
  await api("/api/clue/select", {
    session_code: sessionCode(),
    clue_id: clueId,
    selecting_team: selectingTeam,
  });
}

$("start-game").addEventListener("click", async () => {
  const data = await api("/api/game/start", { config_path: $("config-path").value });
  connectSocket(data.session_code);
});

$("refresh-game").addEventListener("click", async () => {
  const code = sessionCode();
  if (code && code !== "-----") {
    await fetchTeacherState(code);
    connectSocket(code);
    return;
  }
  const response = await fetch("/api/game/latest");
  if (response.ok) {
    state = await response.json();
    render();
    connectSocket(state.session_code);
  }
});

$("score-correct").addEventListener("click", () => {
  api("/api/clue/score", {
    session_code: sessionCode(),
    correct: true,
    team_name: $("score-team").value,
    wager: Number($("wager").value || 0),
  });
});

$("score-wrong").addEventListener("click", () => {
  api("/api/clue/score", {
    session_code: sessionCode(),
    correct: false,
    team_name: $("score-team").value,
    wager: Number($("wager").value || 0),
  });
});

$("reset-buzzer").addEventListener("click", () => {
  api("/api/buzzer/reset", { session_code: sessionCode() });
});

$("save-wagers").addEventListener("click", () => {
  const wagers = {};
  document.querySelectorAll("[data-wager-team]").forEach((input) => {
    wagers[input.dataset.wagerTeam] = Number(input.value || 0);
  });
  api("/api/final/wagers", { session_code: sessionCode(), wagers });
});

$("reveal-final").addEventListener("click", () => {
  api("/api/final/reveal", { session_code: sessionCode() });
});

$("score-final").addEventListener("click", () => {
  const results = {};
  document.querySelectorAll("[data-final-team]").forEach((input) => {
    results[input.dataset.finalTeam] = input.checked;
  });
  api("/api/final/score", { session_code: sessionCode(), results });
});

fetch("/api/game/latest")
  .then((response) => (response.ok ? response.json() : null))
  .then((data) => {
    if (data) {
      state = data;
      render();
      connectSocket(state.session_code);
    }
  });
