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
let finalAdvanceInFlight = false;
let finalRevealSignature = null;
let openRosterTeams = new Set();
let autoRandomAdvanceInFlight = false;
let showEligibleStudents = localStorage.getItem("jeopardy_show_eligible") === "true";

const $ = (id) => document.getElementById(id);
const audioCacheBust = `v=${Date.now()}`;
const audioUrl = (path) => `${path}?${audioCacheBust}`;

const audioFiles = {
  music: new Audio(audioUrl("/static/audio/question_loop.wav")),
  suspense: new Audio(audioUrl("/static/audio/suspense_reveal.wav")),
  results: new Audio(audioUrl("/static/audio/results_ceremony.wav")),
};
audioFiles.music.loop = true;
audioFiles.results.loop = true;
audioFiles.music.volume = 0.18;
audioFiles.suspense.volume = 0.6;
audioFiles.results.volume = 0.285;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

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

function drumroll(duration = 1200) {
  audioFiles.suspense.currentTime = 0;
  audioFiles.suspense.play().catch(() => {});
  return;
  const startedAt = Date.now();
  const interval = window.setInterval(() => {
    playTone(90 + Math.random() * 35, 0.045, "square", 0.055);
    if (Date.now() - startedAt >= duration) {
      window.clearInterval(interval);
    }
  }, 75);
}

function startResultsMusic() {
  if (musicMuted) {
    return;
  }
  audioFiles.results.play().catch(() => {});
}

function stopResultsMusic() {
  audioFiles.results.pause();
}

function ordinal(value) {
  const mod100 = value % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${value}th`;
  const mod10 = value % 10;
  if (mod10 === 1) return `${value}st`;
  if (mod10 === 2) return `${value}nd`;
  if (mod10 === 3) return `${value}rd`;
  return `${value}th`;
}

function startMusic() {
  if (musicMuted) {
    return;
  }
  audioFiles.music.play().catch(() => {});
  return;
  if (musicTimer) {
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
  audioFiles.music.pause();
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
    credentials: "include",
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
  const response = await fetch(`/api/game/${code}?teacher=true`, { credentials: "include" });
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
  $("presentation-link").href = `/board?session=${encodeURIComponent(state.session_code)}`;
  renderBoard();
  renderScores();
  renderStudentRoster();
  renderControls();
  renderFinal();
  renderTimer();
  renderScoreEvent();
  handleAudioState();
}

function defaultTeamName(index) {
  const names = ["Red Team", "Blue Team", "Green Team", "Gold Team", "Purple Team", "Orange Team", "Black Team", "White Team", "Silver Team", "Teal Team"];
  return names[index] || `Team ${index + 1}`;
}

function renderTeamNameFields() {
  const count = Math.max(2, Math.min(10, Number($("team-count").value || 2)));
  $("team-count").value = count;
  const container = $("team-name-fields");
  const existing = Array.from(container.querySelectorAll("[data-team-name-input]")).map((input) => input.value);
  container.innerHTML = "";
  for (let index = 0; index < count; index += 1) {
    const label = document.createElement("label");
    label.textContent = `Team ${index + 1}`;
    const input = document.createElement("input");
    input.dataset.teamNameInput = String(index);
    input.value = existing[index] || defaultTeamName(index);
    label.appendChild(input);
    container.appendChild(label);
  }
}

function teamNamesFromFields() {
  return Array.from(document.querySelectorAll("[data-team-name-input]"))
    .map((input) => input.value.trim())
    .filter(Boolean);
}

async function loadGameConfigs() {
  const select = $("config-path");
  const response = await fetch("/api/game/configs", { credentials: "include" });
  if (!response.ok) {
    select.innerHTML = `<option value="games/example.yaml">games/example.yaml</option>`;
    return;
  }
  const data = await response.json();
  const configs = data.configs?.length ? data.configs : ["games/example.yaml"];
  select.innerHTML = configs.map((path) => `<option value="${escapeHtml(path)}">${escapeHtml(path)}</option>`).join("");
  if (configs.includes("games/cna_modules_1_5.yaml")) {
    select.value = "games/cna_modules_1_5.yaml";
  }
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
    const score = displayScore(team);
    const row = document.createElement("div");
    row.className = "score-row";
    if (score < 0) {
      row.classList.add("score-negative");
    }
    const previousScore = lastScores?.[team.name] ?? 0;
    const delta = score - previousScore;
    if (lastScores && delta < 0) {
      row.classList.add("score-loss");
    }
    const students = team.students?.length ? ` (${team.students.length} students)` : "";
    const deltaLabel = lastScores && delta < 0 ? `<em class="score-delta">-$${Math.abs(delta)}</em>` : "";
    row.innerHTML = `
      <span>${team.name}${team.joined ? students : " (not joined)"}</span>
      <span class="score-total">${deltaLabel}<strong>$${score}</strong></span>
      <input data-score-team="${team.name}" class="score-override-input" type="number" step="100" value="${score}" aria-label="${team.name} score">
    `;
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

  lastScores = Object.fromEntries(state.teams.map((team) => [team.name, displayScore(team)]));
}

function renderStudentRoster() {
  const roster = $("student-roster");
  if (!roster) {
    return;
  }
  roster.innerHTML = "";
  const movesLocked = state.phase !== "board" || Boolean(state.daily_double?.active);
  state.teams.forEach((team) => {
    const group = document.createElement("details");
    group.className = "student-roster-team";
    group.open = openRosterTeams.has(team.name);
    group.addEventListener("toggle", () => {
      if (group.open) {
        openRosterTeams.add(team.name);
      } else {
        openRosterTeams.delete(team.name);
      }
    });
    group.innerHTML = `<summary>${escapeHtml(team.name)} (${team.students?.length || 0})</summary>`;
    if (!team.students?.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No students joined.";
      group.appendChild(empty);
    } else {
      team.students.forEach((student) => {
        const row = document.createElement("div");
        row.className = "student-roster-row";
        const destinations = state.teams
          .filter((candidate) => candidate.name !== team.name)
          .map((candidate) => `<option value="${escapeHtml(candidate.name)}">${escapeHtml(candidate.name)}</option>`)
          .join("");
        row.innerHTML = `
          <span>${escapeHtml(student)}</span>
          <select data-move-destination aria-label="Move ${escapeHtml(student)} to another team" ${movesLocked ? "disabled" : ""}>${destinations}</select>
          <button class="secondary" data-move-student="${escapeHtml(student)}" data-move-from-team="${escapeHtml(team.name)}" ${movesLocked ? "disabled" : ""} title="${movesLocked ? "Students cannot be moved during Daily Double or Final Jeopardy" : "Move student"}">Move</button>
          <button class="secondary" data-remove-student="${escapeHtml(student)}" data-remove-team="${escapeHtml(team.name)}">Remove</button>
        `;
        group.appendChild(row);
      });
    }
    roster.appendChild(group);
  });
}

function eligibleStudents() {
  if (!state.current_clue_id || state.daily_double?.active || state.buzz_winner) {
    return [];
  }
  const eligible = [];
  state.teams.forEach((team) => {
    if (state.steal_active && team.name === state.steal_lockout_team) {
      return;
    }
    const students = team.students || [];
    if (!students.length) {
      return;
    }
    const available = state.steal_active || (
      !state.global_rotation_enabled
      && (team.answered_students || []).length >= students.length
    )
      ? students
      : students.filter((student) => (
        !(team.answered_students || []).some((name) => name.toLowerCase() === student.toLowerCase())
        && !(state.early_buzz_lockouts?.[team.name] || []).some((name) => name.toLowerCase() === student.toLowerCase())
      ));
    available.forEach((student) => eligible.push({ team: team.name, student }));
  });
  return eligible;
}

function renderRotationStatus() {
  const status = $("rotation-status");
  if (!showEligibleStudents) {
    status.innerHTML = "";
    return;
  }
  const eligible = eligibleStudents();
  if (!state.current_clue_id || state.daily_double?.active) {
    status.innerHTML = "";
    return;
  }
  if (state.buzz_winner) {
    status.innerHTML = `<span class="muted">Buzz locked. Reset buzzers to reopen buzzing.</span>`;
    return;
  }
  if (!eligible.length) {
    status.innerHTML = `<strong>No eligible students.</strong> Use Clear student rotation to let everyone buzz again.`;
    return;
  }
  status.innerHTML = `
    <strong>Eligible to buzz:</strong>
    ${eligible.map((item) => `<span class="rotation-pill">${escapeHtml(item.student)} (${escapeHtml(item.team)})</span>`).join("")}
  `;
}

async function saveScoreOverrides() {
  const status = $("score-save-status");
  status.textContent = "Saving score changes...";
  const scores = {};
  document.querySelectorAll("[data-score-team]").forEach((input) => {
    scores[input.dataset.scoreTeam] = Number(input.value || 0);
  });
  try {
    await api("/api/scores", { session_code: sessionCode(), scores });
    status.textContent = "Score changes saved.";
  } catch (error) {
    status.textContent = error.message;
    status.style.color = "var(--danger)";
    throw error;
  }
  status.style.color = "";
}

function showScorePopup(event) {
  const existing = document.querySelector(".score-popup");
  existing?.remove();
  const popup = document.createElement("div");
  const positive = event.delta >= 0;
  popup.className = `score-popup ${positive ? "score-popup-positive" : "score-popup-negative"}`;
  const sign = event.delta > 0 ? "+" : event.delta < 0 ? "-" : "";
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

function teamByName(teamName) {
  return state.teams.find((team) => team.name === teamName) || null;
}

function wagerLimitForTeam(teamName) {
  const score = teamByName(teamName)?.score || 0;
  return score >= 500 ? score : 500;
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
    const dailyDoubleMeta = clue.is_daily_double ? `
      <hr>
      <span class="muted">Leader: ${escapeHtml(state.daily_double.leader || "No leader yet")}</span><br>
      <span class="muted">Wager: ${state.daily_double.wager ?? "Not locked"}</span><br>
      <span class="muted">Submitted by: ${escapeHtml(state.daily_double.submitted_by || "No answer submitted")}</span>
      ${state.daily_double.submission ? `<div class="final-submission">${escapeHtml(state.daily_double.submission)}</div>` : ""}
    ` : "";
    current.innerHTML = `<strong>$${clue.value}</strong><br>${marker}${clue.prompt}<hr><span class="muted">Answer: ${clue.answer}</span>${dailyDoubleMeta}`;
    if (clue.is_daily_double) {
      $("buzz-status").textContent = `Daily Double for ${state.daily_double.team_name || "selected team"}`;
    } else if (state.buzz_winner) {
      const stealLabel = state.steal_active ? " for the steal" : "";
      $("buzz-status").textContent = `${state.buzz_winner_student || "A student"} from ${state.buzz_winner} buzzed first${stealLabel}`;
    } else if (state.steal_active) {
      $("buzz-status").textContent = `${state.steal_lockout_team} is locked out. Remaining teams can buzz or you can continue.`;
    } else {
      $("buzz-status").textContent = lastBuzz || "Waiting for a buzz.";
    }
  }
  dailyControls.classList.toggle("hidden", !clue?.is_daily_double);
  if (clue?.is_daily_double) {
    const wagerInput = $("wager");
    const dailyTeam = state.daily_double.team_name || $("score-team").value;
    const maxWager = wagerLimitForTeam(dailyTeam);
    wagerInput.max = String(maxWager);
    wagerInput.value = state.daily_double.wager ?? Math.min(clue.value, maxWager);
    wagerInput.title = teamByName(dailyTeam)?.score < 500
      ? `This team has less than $500, so the maximum wager is $500.`
      : `Maximum wager: $${maxWager}.`;
  } else {
    $("wager").removeAttribute("max");
    $("wager").title = "";
  }
  $("auto-random-toggle").checked = Boolean(state.auto_random_enabled);
  $("early-buzz-toggle").checked = Boolean(state.early_buzz_penalty_enabled);
  $("global-rotation-toggle").checked = Boolean(state.global_rotation_enabled);
  $("reactions-toggle").checked = Boolean(state.reactions_enabled);
  $("show-eligible-toggle").checked = showEligibleStudents;
  $("reset-buzzer").textContent = "Reset buzzers";
  renderRotationStatus();
}

function timerRemaining() {
  const expiresAt = state.buzz_expires_at
    || state.daily_double?.answer_expires_at
    || (!state.buzz_winner ? state.auto_random_expires_at : null);
  if (!expiresAt) {
    return null;
  }
  return Math.max(0, Math.ceil(expiresAt - Date.now() / 1000));
}

function renderTimer() {
  if (!state) {
    return;
  }
  const timer = $("buzz-timer");
  const remaining = timerRemaining();
  const showAutoRandomTimer = Boolean(state.auto_random_expires_at && !state.buzz_winner);
  if (
    remaining === null
    || (!state.buzz_winner && !state.daily_double?.question_revealed && !showAutoRandomTimer)
  ) {
    timer.classList.add("hidden");
    timer.textContent = "10";
    return;
  }

  timer.classList.remove("hidden");
  timer.classList.toggle("buzz-timer-expired", remaining <= 0);
  timer.textContent = String(remaining);

  const signature = `${state.buzz_winner || state.daily_double?.team_name || "auto-random"}:${state.buzz_winner_student || "timer"}:${state.buzz_expires_at || state.daily_double?.answer_expires_at || state.auto_random_expires_at}`;
  if (remaining <= 0 && expiredTimerSignature !== signature) {
    expiredTimerSignature = signature;
    playTimerExpiredCue();
  }
}

function handleAudioState() {
  const finalOrder = state.final?.reveal_order || [];
  const finalInProgress = state.final?.phase === "complete"
    && state.final?.results_revealed
    && ((state.final.reveal_step || 0) < finalOrder.length || (state.final.reveal_spinning_index ?? null) !== null);
  if (finalInProgress) {
    stopMusic();
    startResultsMusic();
  } else {
    stopResultsMusic();
  }

  const clueId = state.current_clue_id;
  const buzzSignature = state.buzz_winner
    ? `${state.buzz_winner}:${state.buzz_winner_student || ""}`
    : null;

  if (finalInProgress || state.buzz_winner || state.daily_double?.question_revealed || !clueId) {
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
  timerInterval = window.setInterval(() => {
    renderTimer();
    if (state?.final?.phase === "wager" || state?.final?.phase === "answer") {
      updateFinalCountdown();
      advanceFinalIfExpired();
    }
    advanceAutoRandomIfExpired();
  }, 250);
}

async function advanceAutoRandomIfExpired() {
  if (!state || autoRandomAdvanceInFlight || !state.auto_random_expires_at) {
    return;
  }
  if (Date.now() / 1000 < state.auto_random_expires_at) {
    return;
  }
  autoRandomAdvanceInFlight = true;
  try {
    const data = await api("/api/buzzer/auto-random/advance", { session_code: sessionCode() });
    state = data.state || data;
  } finally {
    autoRandomAdvanceInFlight = false;
  }
}

async function advanceFinalIfExpired() {
  if (!state || finalAdvanceInFlight) {
    return;
  }
  const phase = state.final.phase;
  const expiresAt = phase === "wager"
    ? state.final.wager_expires_at
    : phase === "answer"
      ? state.final.answer_expires_at
      : null;
  const studentSubmissionGrace = phase === "answer" ? 1.5 : 0;
  if (!expiresAt || Date.now() / 1000 < expiresAt + studentSubmissionGrace) {
    return;
  }
  finalAdvanceInFlight = true;
  try {
    const data = await api("/api/final/advance", { session_code: sessionCode() });
    state = data.state || data;
  } finally {
    finalAdvanceInFlight = false;
  }
}

function renderFinal() {
  const finalCard = $("final-card");
  const phase = state.final.phase || "pending";
  const finalTimer = phase === "wager"
    ? countdownText(state.final.wager_expires_at)
    : phase === "answer"
      ? countdownText(state.final.answer_expires_at)
      : "";
  const finalPrivate = phase === "pending" || phase === "wager";
  finalCard.innerHTML = finalPrivate
    ? `
      <strong>Final Jeopardy</strong>
      <span id="teacher-final-phase" class="final-phase">${escapeHtml(phase.replace("_", " "))}${finalTimer ? ` · ${finalTimer}` : ""}</span><br>
      Final category, prompt, and answer hidden until wagers are locked.
    `
    : `
      <strong>${escapeHtml(state.final.category)}</strong>
      <span id="teacher-final-phase" class="final-phase">${escapeHtml(phase.replace("_", " "))}${finalTimer ? ` · ${finalTimer}` : ""}</span><br>
      ${state.final.revealed ? escapeHtml(state.final.prompt) : "Final prompt hidden."}
      <hr><span class="muted">Answer: ${escapeHtml(state.final.answer || "")}</span>
      ${phase === "complete" ? `<div id="teacher-winner-stage" class="final-reveal-stage"></div>` : ""}
    `;
  if (phase === "complete") {
    runFinalReveal("teacher-winner-stage");
    const nextButton = $("next-final-reveal");
    if (nextButton) {
      const orderLength = state.final.reveal_order?.length || 0;
      const spinningIndex = state.final.reveal_spinning_index ?? null;
      nextButton.textContent = spinningIndex === null
        ? ((state.final.reveal_step || 0) >= orderLength ? "Reveal Complete" : "Start Next Reveal")
        : "Land Current Place";
      nextButton.disabled = spinningIndex === null && (state.final.reveal_step || 0) >= orderLength;
    }
  } else {
    const nextButton = $("next-final-reveal");
    if (nextButton) {
      nextButton.textContent = "Reveal Next Place";
      nextButton.disabled = true;
    }
  }
  const resetButton = $("reset-final");
  if (resetButton) {
    resetButton.disabled = phase === "pending";
  }
  const calculateButton = $("calculate-winners");
  if (calculateButton) {
    calculateButton.disabled = phase !== "review";
  }

  const wagers = $("final-wagers");
  wagers.innerHTML = "";
  state.teams.forEach((team) => {
    const row = document.createElement("div");
    row.className = "final-wager-row";
    const wager = state.final.wagers[team.name] ?? 0;
    const scored = state.final.scored[team.name];
    const leader = state.final.leaders[team.name] || "No leader yet";
    const submitted = state.final.submissions[team.name] ?? "";
    const autoSubmitted = (state.final.auto_submitted_teams || []).includes(team.name);
    row.innerHTML = `
      <span><strong>${escapeHtml(team.name)}</strong><br><small>Leader: ${escapeHtml(leader)}</small></span>
      <span><small>Wager</small><br><strong>$${wager}</strong></span>
      <label><input data-final-team="${team.name}" type="checkbox" ${scored === true ? "checked" : ""}> Correct</label>
      <div class="final-submission">
        ${submitted ? escapeHtml(submitted) : "<span class=\"muted\">No answer entered</span>"}
        ${autoSubmitted ? "<br><small class=\"muted\">Auto-submitted when time expired</small>" : ""}
      </div>
    `;
    wagers.appendChild(row);
  });
}

function updateFinalCountdown() {
  const phase = state?.final?.phase;
  const label = $("teacher-final-phase");
  if (!phase || !label) {
    return;
  }
  const finalTimer = phase === "wager"
    ? countdownText(state.final.wager_expires_at)
    : phase === "answer"
      ? countdownText(state.final.answer_expires_at)
      : "";
  label.textContent = `${phase.replace("_", " ")}${finalTimer ? ` · ${finalTimer}` : ""}`;
}

function runFinalReveal(stageId) {
  const order = state.final.reveal_order || [];
  const revealStep = state.final.reveal_step || 0;
  const spinningIndex = state.final.reveal_spinning_index ?? null;
  const signature = `${stageId}:${order.join("|")}:${JSON.stringify(state.final.final_scores || {})}:${revealStep}:${spinningIndex}`;
  if (finalRevealSignature === signature) {
    return;
  }
  finalRevealSignature = signature;
  const stage = $(stageId);
  stage.innerHTML = "";
  const teams = order.map((name) => state.teams.find((team) => team.name === name)).filter(Boolean);
  if (revealStep === 0 && spinningIndex === null) {
    stage.innerHTML = `
      <div class="winner-row winner-row-pending">
        <small>Final results ready</small>
        <span>Reveal the next place</span>
        <strong>$----</strong>
      </div>
    `;
    return;
  }
  teams.forEach((team, index) => {
    if (index >= revealStep && index !== spinningIndex) {
      return;
    }
    const place = teams.length - index;
    const shouldSpin = index === spinningIndex;
    const finalScore = finalScoreFor(team);
    const row = document.createElement("div");
    row.className = `winner-row ${index === teams.length - 1 ? "winner-row-champion" : ""}`;
    if (!shouldSpin) {
      row.innerHTML = `<small>In ${ordinal(place)} place we have:</small><span>${escapeHtml(team.name)}</span><strong>$${finalScore}</strong>`;
      stage.appendChild(row);
      return;
    }
    row.classList.add("winner-row-revealing");
    row.innerHTML = `<small>In ${ordinal(place)} place we have:</small><span class="winner-name-hidden">????</span><strong data-slot-machine="true">$0000</strong>`;
    stage.appendChild(row);
    startSlotSpin(row.querySelector("[data-slot-machine]"));
  });
}

function startSlotSpin(target, onDone) {
  if (!target) return;
  let ticks = 0;
  const interval = window.setInterval(() => {
    if (!document.body.contains(target)) {
      window.clearInterval(interval);
      return;
    }
    ticks += 1;
    target.textContent = `$${Math.floor(Math.random() * 9999)}`;
    if (onDone && ticks > 22) {
      window.clearInterval(interval);
      onDone?.();
    }
  }, 70);
}

function finalScoreFor(team) {
  return state.final?.final_scores?.[team.name] ?? team.score;
}

function displayScore(team) {
  return team.score;
}

function countdownText(expiresAt) {
  if (!expiresAt) {
    return "";
  }
  const seconds = Math.max(0, Math.ceil(expiresAt - Date.now() / 1000));
  const minutes = Math.floor(seconds / 60);
  const remainder = String(seconds % 60).padStart(2, "0");
  return `${minutes}:${remainder}`;
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
    ? `Start a new game and create a new room code? Current students will need to join the new code.`
    : "Create a new game room?";
  if (!window.confirm(message)) {
    return;
  }
  const teamNames = teamNamesFromFields();
  if (teamNames.length < 2 || teamNames.length > 10) {
    window.alert("Enter between 2 and 10 team names.");
    return;
  }
  const data = await api("/api/game/start", {
    config_path: $("config-path").value,
    team_names: teamNames,
    previous_session_code: code && code !== "-----" ? code : null,
  });
  connectSocket(data.session_code);
});

$("refresh-game").addEventListener("click", async () => {
  const code = sessionCode();
  if (code && code !== "-----") {
    await fetchTeacherState(code);
    connectSocket(code);
    return;
  }
  const response = await fetch("/api/game/latest", { credentials: "include" });
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

$("reset-rotation").addEventListener("click", () => {
  api("/api/buzzer/reset-rotation", { session_code: sessionCode() });
});

$("random-buzz").addEventListener("click", () => {
  api("/api/buzzer/random", { session_code: sessionCode() });
});

$("auto-random-toggle").addEventListener("change", (event) => {
  api("/api/buzzer/auto-random", {
    session_code: sessionCode(),
    enabled: event.target.checked,
  });
});

$("early-buzz-toggle").addEventListener("change", (event) => {
  api("/api/buzzer/early-penalty", {
    session_code: sessionCode(),
    enabled: event.target.checked,
  });
});

$("global-rotation-toggle").addEventListener("change", (event) => {
  api("/api/buzzer/global-rotation", {
    session_code: sessionCode(),
    enabled: event.target.checked,
  });
});

$("show-eligible-toggle").addEventListener("change", (event) => {
  showEligibleStudents = event.target.checked;
  localStorage.setItem("jeopardy_show_eligible", String(showEligibleStudents));
  renderRotationStatus();
});

$("reactions-toggle").addEventListener("change", (event) => {
  api("/api/reactions/toggle", {
    session_code: sessionCode(),
    enabled: event.target.checked,
  });
});

$("continue-clue").addEventListener("click", () => {
  api("/api/clue/continue", { session_code: sessionCode() });
});

$("save-scores").addEventListener("click", () => {
  saveScoreOverrides().catch(() => {});
});

$("student-roster").addEventListener("click", (event) => {
  const moveButton = event.target.closest("[data-move-student]");
  if (moveButton) {
    const row = moveButton.closest(".student-roster-row");
    const student = moveButton.dataset.moveStudent;
    const fromTeam = moveButton.dataset.moveFromTeam;
    const toTeam = row.querySelector("[data-move-destination]").value;
    if (!toTeam || !window.confirm(`Move ${student} from ${fromTeam} to ${toTeam}?`)) {
      return;
    }
    api("/api/students/move", {
      session_code: sessionCode(),
      from_team_name: fromTeam,
      to_team_name: toTeam,
      student_name: student,
    }).catch((error) => window.alert(error.message));
    return;
  }
  const button = event.target.closest("[data-remove-student]");
  if (!button) {
    return;
  }
  const student = button.dataset.removeStudent;
  const team = button.dataset.removeTeam;
  if (!window.confirm(`Remove ${student} from ${team}?`)) {
    return;
  }
  api("/api/students/remove", {
    session_code: sessionCode(),
    team_name: team,
    student_name: student,
  });
});

$("team-count").addEventListener("change", renderTeamNameFields);

$("lock-daily-double").addEventListener("click", () => {
  api("/api/daily-double/wager", {
    session_code: sessionCode(),
    team_name: $("score-team").value,
    wager: Number($("wager").value || 0),
  });
});

$("reset-final").addEventListener("click", () => {
  if (!window.confirm("Reset Final Jeopardy? Students will need to lock wagers and submit answers again.")) {
    return;
  }
  api("/api/final/reset", { session_code: sessionCode() })
    .catch((error) => window.alert(error.message));
});

$("calculate-winners").addEventListener("click", () => {
  const results = {};
  document.querySelectorAll("[data-final-team]").forEach((input) => {
    results[input.dataset.finalTeam] = input.checked;
  });
  api("/api/final/score", { session_code: sessionCode(), results })
    .then(() => api("/api/final/calculate", { session_code: sessionCode() }))
    .catch((error) => window.alert(error.message));
});

$("next-final-reveal").addEventListener("click", () => {
  api("/api/final/reveal-next", { session_code: sessionCode() })
    .catch((error) => window.alert(error.message));
});

$("return-top").addEventListener("click", () => {
  window.scrollTo({ top: 0, behavior: "smooth" });
});

renderTeamNameFields();
loadGameConfigs();

fetch("/api/game/latest", { credentials: "include" })
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
