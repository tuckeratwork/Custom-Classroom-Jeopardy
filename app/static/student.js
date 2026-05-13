let sessionCode = localStorage.getItem("jeopardy_session") || "";
let teamName = localStorage.getItem("jeopardy_team") || "";
let studentName = localStorage.getItem("jeopardy_student") || "";
let socket = null;
let state = null;
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

function playTone(frequency, duration, type = "sine", volume = 0.05, offset = 0) {
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

function startMusic() {
  if (musicMuted || musicTimer) {
    return;
  }
  const notes = [196, 247, 294, 330, 294, 247];
  musicTimer = window.setInterval(() => {
    playTone(notes[musicStep % notes.length], 0.32, "sine", 0.018);
    musicStep += 1;
  }, 420);
}

function playTimerExpiredCue() {
  playTone(440, 0.16, "square", 0.08);
  playTone(330, 0.16, "square", 0.08, 0.18);
  playTone(220, 0.28, "sawtooth", 0.08, 0.36);
}

function playCorrectCue() {
  [523, 659, 784].forEach((frequency, index) => playTone(frequency, 0.14, "triangle", 0.08, index * 0.1));
}

function playWrongCue() {
  [220, 165, 110].forEach((frequency, index) => playTone(frequency, 0.18, "sawtooth", 0.07, index * 0.12));
}

function stopMusic() {
  if (musicTimer) {
    window.clearInterval(musicTimer);
    musicTimer = null;
  }
}

function showBuzzer() {
  $("join-card").classList.add("hidden");
  $("buzzer-card").classList.remove("hidden");
  $("student-session").textContent = `Session ${sessionCode}`;
  $("student-team").textContent = `${studentName} - ${teamName}`;
}

function showJoin() {
  stopMusic();
  if (socket) {
    socket.close();
    socket = null;
  }
  state = null;
  $("buzzer-card").classList.add("hidden");
  $("join-card").classList.remove("hidden");
  $("join-code").value = sessionCode;
  $("team-name").value = teamName;
  $("student-name").value = studentName;
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

function myTeam() {
  return state?.teams?.find((team) => team.name === teamName) || null;
}

function canBuzz(clue) {
  if (!clue || clue.is_daily_double || state.buzz_winner) {
    return false;
  }
  const team = myTeam();
  if (!team || !studentName) {
    return false;
  }
  const answered = team.answered_students || [];
  const students = team.students || [];
  return answered.length >= students.length || !answered.some((name) => name.toLowerCase() === studentName.toLowerCase());
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

function showScorePopup(event) {
  const existing = document.querySelector(".score-popup");
  existing?.remove();
  const popup = document.createElement("div");
  const positive = event.delta >= 0;
  popup.className = `score-popup ${positive ? "score-popup-positive" : "score-popup-negative"}`;
  const sign = positive ? "+" : "-";
  popup.textContent = `${event.team_name} ${sign}$${Math.abs(event.delta)}`;
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
    stopMusic();
  } else if (clue.is_daily_double) {
    $("student-clue").textContent = "Daily Double. The teacher will manage the wager.";
    button.disabled = true;
    startMusic();
  } else {
    $("student-clue").textContent = clue.prompt;
    button.disabled = !canBuzz(clue);
    if (state.buzz_winner) {
      stopMusic();
    } else {
      startMusic();
    }
  }

  if (state.buzz_winner === teamName) {
    $("student-status").textContent = state.buzz_winner_student === studentName
      ? "You buzzed first."
      : `${state.buzz_winner_student || "A teammate"} buzzed first.`;
  } else if (state.buzz_winner) {
    $("student-status").textContent = `${state.buzz_winner_student || "A student"} from ${state.buzz_winner} buzzed first.`;
  } else if (clue && !clue.is_daily_double && !canBuzz(clue)) {
    $("student-status").textContent = "Wait for the rest of your team to answer.";
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
    if (team.score < 0) {
      row.classList.add("score-negative");
    }
    row.innerHTML = `<span>${team.name}</span><strong>$${team.score}</strong>`;
    scores.appendChild(row);
  });

  renderTimer();
  renderScoreEvent();
}

$("join-code").value = sessionCode;
$("team-name").value = teamName;
$("student-name").value = studentName;

$("join-game").addEventListener("click", async () => {
  sessionCode = $("join-code").value.trim().toUpperCase();
  teamName = $("team-name").value.trim();
  studentName = $("student-name").value.trim();
  $("join-error").textContent = "";
  const response = await fetch("/api/join", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_code: sessionCode, team_name: teamName, student_name: studentName }),
  });
  const data = await response.json();
  if (!response.ok) {
    $("join-error").textContent = data.detail || "Unable to join.";
    return;
  }
  localStorage.setItem("jeopardy_session", sessionCode);
  localStorage.setItem("jeopardy_team", teamName);
  localStorage.setItem("jeopardy_student", studentName);
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
    body: JSON.stringify({ session_code: sessionCode, team_name: teamName, student_name: studentName }),
  });
  if (!response.ok) {
    const data = await response.json();
    $("student-status").textContent = data.detail || "Buzz did not register.";
    $("buzz-button").disabled = false;
  }
});

$("change-game").addEventListener("click", () => {
  localStorage.removeItem("jeopardy_session");
  sessionCode = "";
  showJoin();
});

if (sessionCode && teamName && studentName) {
  showBuzzer();
  connectSocket();
}

$("mute-music").checked = musicMuted;
$("mute-music").addEventListener("change", (event) => {
  musicMuted = event.target.checked;
  localStorage.setItem("jeopardy_mute_music", String(musicMuted));
  if (musicMuted) {
    stopMusic();
  } else if (currentClue() && !state?.buzz_winner) {
    startMusic();
  }
});

if (!timerInterval) {
  timerInterval = window.setInterval(renderTimer, 250);
}
