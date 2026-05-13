let state = null;
let socket = null;
let lastScores = null;
let lastBuzzSignature = null;
let lastCurrentClueId = null;
let audioContext = null;
let musicTimer = null;
let musicStep = 0;
let musicMuted = localStorage.getItem("jeopardy_mute_music") === "true";
let timerInterval = null;
let expiredTimerSignature = null;
let lastScoreEventId = null;
let hasSeenScoreState = false;

const $ = (id) => document.getElementById(id);

function getAudioContext() {
  if (!audioContext) {
    audioContext = new (window.AudioContext || window.webkitAudioContext)();
  }
  if (audioContext.state === "suspended") {
    audioContext.resume();
  }
  return audioContext;
}

function playTone(frequency, duration, type = "sine", volume = 0.08, offset = 0) {
  const context = getAudioContext();
  const oscillator = context.createOscillator();
  const gain = context.createGain();
  const start = context.currentTime + offset;
  oscillator.type = type;
  oscillator.frequency.value = frequency;
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(volume, start + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.start(start);
  oscillator.stop(start + duration + 0.04);
}

function playBuzzerCue() {
  playTone(130, 0.18, "sawtooth", 0.12);
  playTone(92, 0.22, "square", 0.08, 0.08);
}

function playCorrectCue() {
  [523, 659, 784].forEach((frequency, index) => playTone(frequency, 0.14, "triangle", 0.1, index * 0.1));
}

function playWrongCue() {
  [220, 165, 110].forEach((frequency, index) => playTone(frequency, 0.18, "sawtooth", 0.09, index * 0.12));
}

function startMusic() {
  if (musicMuted || musicTimer) {
    return;
  }
  const notes = [196, 247, 294, 330, 294, 247];
  musicTimer = window.setInterval(() => {
    playTone(notes[musicStep % notes.length], 0.32, "sine", 0.025);
    musicStep += 1;
  }, 420);
}

function playTimerExpiredCue() {
  playTone(440, 0.16, "square", 0.1);
  playTone(330, 0.16, "square", 0.1, 0.18);
  playTone(220, 0.28, "sawtooth", 0.1, 0.36);
}

function stopMusic() {
  if (musicTimer) {
    window.clearInterval(musicTimer);
    musicTimer = null;
  }
}

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
  renderTimer();
  renderScoreEvent();
  handleAudioState();
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
    if (team.score < 0) {
      row.classList.add("score-negative");
    }
    const previousScore = lastScores?.[team.name] ?? 0;
    const delta = team.score - previousScore;
    if (lastScores && delta < 0) {
      row.classList.add("score-loss");
    }
    const students = team.students?.length ? ` (${team.students.length} students)` : "";
    const deltaLabel = lastScores && delta < 0 ? `<em class="score-delta">-$${Math.abs(delta)}</em>` : "";
    row.innerHTML = `<span>${team.name}${team.joined ? students : " (not joined)"}</span><span class="score-total">${deltaLabel}<strong>$${team.score}</strong></span>`;
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

  lastScores = Object.fromEntries(state.teams.map((team) => [team.name, team.score]));
}

function showScorePopup(event) {
  const existing = document.querySelector(".score-popup");
  existing?.remove();
  const popup = document.createElement("div");
  const positive = event.delta >= 0;
  popup.className = `score-popup ${positive ? "score-popup-positive" : "score-popup-negative"}`;
  const sign = positive ? "+" : "-";
  const student = event.student_name ? `${event.student_name}, ` : "";
  popup.textContent = `${student}${event.team_name} ${sign}$${Math.abs(event.delta)}`;
  document.body.appendChild(popup);
  window.setTimeout(() => popup.remove(), 2200);
}

function renderScoreEvent() {
  if (!hasSeenScoreState) {
    hasSeenScoreState = true;
    lastScoreEventId = state.score_event?.id ?? null;
    return;
  }
  if (!state.score_event || state.score_event.id === lastScoreEventId) {
    return;
  }
  lastScoreEventId = state.score_event.id;
  showScorePopup(state.score_event);
  if (state.score_event.correct) {
    playCorrectCue();
  } else {
    playWrongCue();
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
  const lastBuzz = state.last_buzz_student && state.last_buzz_team
    ? `Last buzz: ${state.last_buzz_student} from ${state.last_buzz_team}`
    : "";
  if (!clue) {
    current.classList.add("muted");
    current.textContent = "No clue selected.";
    $("buzz-status").textContent = lastBuzz;
  } else {
    current.classList.remove("muted");
    const marker = clue.is_daily_double ? "Daily Double: " : "";
    current.innerHTML = `<strong>$${clue.value}</strong><br>${marker}${clue.prompt}<hr><span class="muted">Answer: ${clue.answer}</span>`;
    $("buzz-status").textContent = clue.is_daily_double
      ? `Daily Double for ${state.daily_double.team_name || "selected team"}`
      : state.buzz_winner
        ? `${state.buzz_winner_student || "A student"} from ${state.buzz_winner} buzzed first`
        : lastBuzz || "Waiting for a buzz.";
  }
  dailyControls.classList.toggle("hidden", !clue?.is_daily_double);
}

function timerRemaining() {
  if (!state.buzz_expires_at) {
    return null;
  }
  return Math.max(0, Math.ceil(state.buzz_expires_at - Date.now() / 1000));
}

function renderTimer() {
  if (!state) {
    return;
  }
  const timer = $("buzz-timer");
  const remaining = timerRemaining();
  if (remaining === null || !state.buzz_winner) {
    timer.classList.add("hidden");
    timer.textContent = "10";
    return;
  }

  timer.classList.remove("hidden");
  timer.classList.toggle("buzz-timer-expired", remaining <= 0);
  timer.textContent = String(remaining);

  const signature = `${state.buzz_winner}:${state.buzz_winner_student || ""}:${state.buzz_expires_at}`;
  if (remaining <= 0 && expiredTimerSignature !== signature) {
    expiredTimerSignature = signature;
    playTimerExpiredCue();
  }
}

function handleAudioState() {
  const clueId = state.current_clue_id;
  const buzzSignature = state.buzz_winner
    ? `${state.buzz_winner}:${state.buzz_winner_student || ""}`
    : null;

  if (state.buzz_winner || !clueId) {
    stopMusic();
  } else if (!musicTimer) {
    startMusic();
  }

  if (buzzSignature && buzzSignature !== lastBuzzSignature) {
    playBuzzerCue();
  }

  lastCurrentClueId = clueId;
  lastBuzzSignature = buzzSignature;
}

function startTimerLoop() {
  if (timerInterval) {
    return;
  }
  timerInterval = window.setInterval(renderTimer, 250);
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
  const code = sessionCode();
  const message = code && code !== "-----"
    ? `Reset the game and create a new room code? Current students will need to rejoin with the new code.`
    : "Create a new game room?";
  if (!window.confirm(message)) {
    return;
  }
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

$("mute-music").checked = musicMuted;
$("mute-music").addEventListener("change", (event) => {
  musicMuted = event.target.checked;
  localStorage.setItem("jeopardy_mute_music", String(musicMuted));
  if (musicMuted) {
    stopMusic();
  } else if (state?.current_clue_id && !state?.buzz_winner) {
    startMusic();
  }
});

startTimerLoop();
