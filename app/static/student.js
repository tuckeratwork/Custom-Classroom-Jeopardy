let sessionCode = localStorage.getItem("jeopardy_session") || "";
let teamName = localStorage.getItem("jeopardy_team") || "";
let studentName = localStorage.getItem("jeopardy_student") || "";
let participantId = localStorage.getItem("jeopardy_participant_id") || "";
if (!participantId) {
  participantId = globalThis.crypto?.randomUUID?.()
    || `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
  localStorage.setItem("jeopardy_participant_id", participantId);
}
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
let finalAdvanceInFlight = false;
let finalRevealSignature = null;
let finalActionKey = null;
let studentMoveNoticeTimer = null;
let participantClaimAttempted = false;

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
audioFiles.music.volume = 0.165;
audioFiles.suspense.volume = 0.55;
audioFiles.results.volume = 0.27;

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizedIdentity(value) {
  return String(value || "").trim().replace(/\s+/g, " ").toLowerCase();
}

function sameIdentity(left, right) {
  return normalizedIdentity(left) === normalizedIdentity(right);
}

const supportEmojis = ["👏", "🔥", "💪", "🙌", "⭐"];
const oppositionEmojis = ["🤔", "💀", "🍅", "💩", "🥀"];

function imageUrl(path) {
  if (!path) {
    return "";
  }
  if (/^(https?:)?\/\//.test(path) || path.startsWith("/")) {
    return path;
  }
  return `/game-assets/${encodeURI(path)}`;
}

function clueContentMarkup(clue) {
  const image = imageUrl(clue?.image);
  return `
    <div>${escapeHtml(clue?.prompt || "")}</div>
    ${image ? `<img class="question-image student-question-image" src="${escapeHtml(image)}" alt="">` : ""}
  `;
}

function spawnReaction(emoji, side) {
  const stream = $(side === "support" ? "reaction-stream-right" : "reaction-stream-left");
  if (!stream) {
    return;
  }
  const item = document.createElement("span");
  item.className = "floating-reaction";
  item.textContent = emoji;
  item.style.left = `${20 + Math.random() * 55}%`;
  item.style.animationDuration = `${1800 + Math.random() * 700}ms`;
  stream.appendChild(item);
  window.setTimeout(() => item.remove(), 2800);
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

function drumroll(duration = 1200) {
  audioFiles.suspense.currentTime = 0;
  audioFiles.suspense.play().catch(() => {});
  return;
  const startedAt = Date.now();
  const interval = window.setInterval(() => {
    playTone(90 + Math.random() * 35, 0.045, "square", 0.045);
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

function stopMusic() {
  audioFiles.music.pause();
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
  loadTeamsForCode();
  $("student-name").value = studentName;
}

function handleSessionReset(newCode) {
  sessionCode = newCode || "";
  localStorage.removeItem("jeopardy_session");
  $("join-code").value = sessionCode;
  $("join-error").textContent = sessionCode
    ? "The game was reset. Join with the new code."
    : "The game was reset. Enter the new session code.";
  showJoin();
}

function handleSessionEnded(newCode) {
  sessionCode = "";
  teamName = "";
  localStorage.removeItem("jeopardy_session");
  localStorage.removeItem("jeopardy_team");
  $("join-code").value = newCode || "";
  $("join-error").textContent = newCode
    ? "This game ended. Join the new game code when your teacher tells you to."
    : "This game ended. Enter a new session code to join.";
  showJoin();
}

function handleStudentRemoved(message) {
  if (
    message.team_name === teamName
    && String(message.student_name || "").toLowerCase() === studentName.toLowerCase()
  ) {
    localStorage.removeItem("jeopardy_session");
    localStorage.removeItem("jeopardy_team");
    $("join-error").textContent = "You were removed from that game. Ask your teacher before rejoining.";
    showJoin();
  }
}

function handleStudentMoved(message) {
  if (
    message.from_team_name !== teamName
    || String(message.student_name || "").toLowerCase() !== studentName.toLowerCase()
  ) {
    return;
  }
  teamName = message.to_team_name;
  localStorage.setItem("jeopardy_team", teamName);
  $("student-team").textContent = `${studentName} - ${teamName}`;
  const notice = $("student-move-notice");
  notice.textContent = `Your teacher moved you to ${teamName}.`;
  notice.classList.remove("hidden");
  window.clearTimeout(studentMoveNoticeTimer);
  studentMoveNoticeTimer = window.setTimeout(() => notice.classList.add("hidden"), 6000);
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
    } else if (message.type === "session_reset") {
      handleSessionReset(message.new_session_code);
    } else if (message.type === "session_ended") {
      handleSessionEnded(message.new_session_code);
    } else if (message.type === "student_removed") {
      handleStudentRemoved(message);
    } else if (message.type === "student_moved") {
      handleStudentMoved(message);
    } else if (message.type === "emoji_reaction") {
      if (amActiveResponder()) {
        spawnReaction(message.emoji, message.side);
      }
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

function participantMembership() {
  for (const team of state?.teams || []) {
    for (const [name, storedId] of Object.entries(team.student_ids || {})) {
      if (storedId === participantId) {
        return { team, studentName: name };
      }
    }
  }
  return null;
}

function storedParticipantForStudent(team, name) {
  const entry = Object.entries(team?.student_ids || {})
    .find(([student]) => sameIdentity(student, name));
  return entry?.[1] || null;
}

async function claimLegacyMembership() {
  if (participantClaimAttempted) {
    return;
  }
  participantClaimAttempted = true;
  try {
    const response = await fetch("/api/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_code: sessionCode,
        team_name: teamName,
        student_name: studentName,
        participant_id: participantId,
      }),
    });
    if (response.ok) {
      const data = await response.json();
      state = data.state;
      render();
    }
  } catch (_error) {
    // The websocket can continue showing the legacy membership for this page load.
  }
}

function amActiveResponder() {
  return state?.buzz_winner === teamName && state?.buzz_winner_student === studentName;
}

function amEarlyBuzzLocked() {
  return (state?.early_buzz_lockouts?.[teamName] || [])
    .some((name) => sameIdentity(name, studentName));
}

function teamCompletedRotation(team) {
  const students = team?.students || [];
  const answered = team?.answered_students || [];
  return students.length > 0 && students.every((student) => (
    answered.some((name) => sameIdentity(name, student))
  ));
}

function pendingGlobalRotationTeams(team) {
  if (!state?.global_rotation_enabled || !teamCompletedRotation(team)) {
    return [];
  }
  return state.teams
    .filter((candidate) => candidate.name !== team.name && !teamCompletedRotation(candidate))
    .filter((candidate) => (candidate.students || []).length > 0)
    .map((candidate) => candidate.name);
}

async function loadTeamsForCode() {
  const code = $("join-code").value.trim().toUpperCase();
  const select = $("team-name");
  if (!code) {
    select.innerHTML = teamName ? `<option value="${escapeHtml(teamName)}">${escapeHtml(teamName)}</option>` : "";
    return;
  }
  const response = await fetch(`/api/game/${code}/teams`);
  if (!response.ok) {
    select.innerHTML = teamName ? `<option value="${escapeHtml(teamName)}">${escapeHtml(teamName)}</option>` : "";
    return;
  }
  const data = await response.json();
  select.innerHTML = (data.teams || [])
    .map((name) => `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`)
    .join("");
  if (teamName && [...select.options].some((option) => option.value === teamName)) {
    select.value = teamName;
  }
}

function finalWagerLimit() {
  const score = myTeam()?.score || 0;
  return score >= 500 ? score : 500;
}

function wagerLimit() {
  return finalWagerLimit();
}

function isDailyDoubleLeader() {
  return state?.daily_double?.team_name === teamName && state.daily_double?.leader === studentName;
}

function canBuzz(clue) {
  if (!clue || clue.is_daily_double || state.buzz_winner) {
    return false;
  }
  const team = myTeam();
  if (!team || !studentName) {
    return false;
  }
  if (state.steal_active) {
    return team.name !== state.steal_lockout_team;
  }
  if (amEarlyBuzzLocked()) {
    return false;
  }
  const answered = team.answered_students || [];
  const students = team.students || [];
  return (
    (!state.global_rotation_enabled && answered.length >= students.length)
    || !answered.some((name) => name.toLowerCase() === studentName.toLowerCase())
  );
}

function renderOpeningCountdown() {
  const countdown = $("buzzer-opening");
  const clue = currentClue();
  const remaining = state?.buzz_open_at
    ? Math.max(0, Math.ceil(state.buzz_open_at - Date.now() / 1000))
    : 0;
  const visible = Boolean(
    clue
    && !clue.is_daily_double
    && !state.steal_active
    && !state.buzz_winner
    && remaining > 0
  );
  countdown.classList.toggle("hidden", !visible);
  if (visible) {
    countdown.innerHTML = `Buzzers opening in <strong>${remaining}</strong>`;
  }
}

function renderRemainingTeammates(clue) {
  const panel = $("remaining-teammates");
  panel.classList.add("hidden");
  panel.innerHTML = "";
  const team = myTeam();
  if (!team || !clue || clue.is_daily_double || state.steal_active || state.buzz_winner) {
    return;
  }
  const students = team.students || [];
  const answered = team.answered_students || [];
  if (state.global_rotation_enabled && teamCompletedRotation(team)) {
    const pendingTeams = pendingGlobalRotationTeams(team);
    if (pendingTeams.length) {
      const teamList = pendingTeams.length === 1
        ? pendingTeams[0]
        : pendingTeams.length === 2
          ? `${pendingTeams[0]} and ${pendingTeams[1]}`
          : `${pendingTeams.slice(0, -1).join(", ")}, and ${pendingTeams.at(-1)}`;
      panel.innerHTML = `Everyone on your team has buzzed in. Waiting for the remaining participants on <strong>${escapeHtml(teamList)}</strong> to buzz in before all buzzers are reset.`;
      panel.classList.remove("hidden");
    }
    return;
  }
  const lockouts = state.early_buzz_lockouts?.[team.name] || [];
  const rotationReset = !state.global_rotation_enabled && answered.length >= students.length;
  const remaining = students.filter((student) => (
    (rotationReset || !answered.some((name) => name.toLowerCase() === student.toLowerCase()))
    && !lockouts.some((name) => name.toLowerCase() === student.toLowerCase())
  ));
  if (!remaining.length || remaining.length > 3) {
    return;
  }
  const names = remaining.map((name) => (
    name.toLowerCase() === studentName.toLowerCase() ? "You" : name
  ));
  panel.innerHTML = `<strong>Still eligible for your team:</strong> ${names.map(escapeHtml).join(", ")}`;
  panel.classList.remove("hidden");
}

function timerRemaining() {
  const expiresAt = state.buzz_expires_at || state.daily_double?.answer_expires_at;
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
  const card = $("buzzer-card");
  const remaining = timerRemaining();
  card.classList.remove("buzzer-card-warning", "buzzer-card-expired");
  if (remaining === null || (!state.buzz_winner && !state.daily_double?.question_revealed)) {
    timer.classList.add("hidden");
    timer.textContent = "10";
    return;
  }

  timer.classList.remove("hidden");
  timer.classList.toggle("buzz-timer-expired", remaining <= 0);
  timer.textContent = String(remaining);
  if (amActiveResponder()) {
    card.classList.toggle("buzzer-card-warning", remaining > 0 && remaining <= 2);
    card.classList.toggle("buzzer-card-expired", remaining <= 0);
  }

  const signature = `${state.buzz_winner || state.daily_double?.team_name}:${state.buzz_winner_student || "daily-double"}:${state.buzz_expires_at || state.daily_double?.answer_expires_at}`;
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
  const sign = event.delta > 0 ? "+" : event.delta < 0 ? "-" : "";
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

function renderFinalLeaderboard() {
  const board = $("final-leaderboard");
  const teams = [...state.teams].sort((left, right) => displayScore(right) - displayScore(left));
  board.innerHTML = teams.map((team, index) => `
    <div class="final-leaderboard-row">
      <span>${index + 1}. ${escapeHtml(team.name)}</span>
      <strong>$${displayScore(team)}</strong>
    </div>
  `).join("");
}

function renderDailyDoubleAction(clue) {
  const action = $("daily-double-action");
  action.classList.add("hidden");
  action.innerHTML = "";
  if (!clue?.is_daily_double || !state.daily_double?.active) {
    return;
  }
  action.classList.remove("hidden");
  const leader = state.daily_double.leader || "Waiting for leader";
  const isLeader = isDailyDoubleLeader();
  const wagerLocked = state.daily_double.wager !== null && state.daily_double.wager !== undefined;
  const submitted = state.daily_double.submission !== null && state.daily_double.submission !== undefined;
  if (!isLeader) {
    action.innerHTML = `<p>Daily Double leader: ${escapeHtml(leader)}</p>`;
    return;
  }
  if (!wagerLocked) {
    const maxWager = wagerLimit();
    const teamScore = myTeam()?.score || 0;
    action.innerHTML = `
      ${teamScore < 500 ? `<p class="notice-text">Your team has less than $500, so you may wager up to $500.</p>` : ""}
      <p id="daily-double-error" class="error-text"></p>
      <label for="daily-double-wager-input">Daily Double wager</label>
      <input id="daily-double-wager-input" type="number" min="0" max="${maxWager}" value="${Math.min(clue.value, maxWager)}" inputmode="numeric">
      <button id="submit-daily-double-wager">Lock wager</button>
    `;
    $("submit-daily-double-wager")?.addEventListener("click", submitDailyDoubleWager);
    return;
  }
  if (!state.daily_double.question_revealed) {
    action.innerHTML = `<p>Waiting for the Daily Double question.</p>`;
    return;
  }
  if (submitted) {
    action.innerHTML = `<p>Your team's Daily Double answer is submitted.</p>`;
    return;
  }
  action.innerHTML = `
    <p id="daily-double-error" class="error-text"></p>
    <label for="daily-double-answer-input">Daily Double answer</label>
    <textarea id="daily-double-answer-input" rows="3"></textarea>
    <button id="submit-daily-double-answer">Submit answer</button>
  `;
  $("submit-daily-double-answer")?.addEventListener("click", submitDailyDoubleAnswer);
}

function renderFinalAction() {
  const action = $("final-action");
  const previousWager = $("final-wager-input")?.value;
  const previousAnswer = $("final-answer-input")?.value;
  const previousError = $("final-submit-error")?.textContent || "";
  const phase = state.final.phase;
  const leader = state.final.leaders[teamName];
  const isLeader = leader === studentName;
  const teamScore = myTeam()?.score || 0;
  const maxWager = finalWagerLimit();
  const locked = state.final.wagers[teamName] !== undefined;
  const submitted = state.final.submissions[teamName] !== undefined;
  const renderKey = `${phase}:${leader || ""}:${isLeader}:${locked}:${submitted}:${teamScore}:${state.final.reveal_step || 0}:${state.final.reveal_spinning_index ?? null}`;
  if (finalActionKey === renderKey && (phase === "wager" || phase === "answer")) {
    return;
  }
  finalActionKey = renderKey;

  if (phase === "wager") {
    action.innerHTML = `
      <h2>Final Jeopardy</h2>
      <p>Lock your wager before the category and prompt are revealed.</p>
      <p class="muted">Team leader: ${escapeHtml(leader || "Waiting for leader")}</p>
      <p id="student-final-countdown" class="final-countdown">${countdownText(state.final.wager_expires_at)}</p>
      ${teamScore < 500 ? `<p class="notice-text">Your team has less than $500, so you may wager up to $500.</p>` : ""}
      <p id="final-submit-error" class="error-text"></p>
      ${isLeader && !locked ? `
        <label for="final-wager-input">Wager</label>
        <input id="final-wager-input" type="number" min="0" max="${maxWager}" value="${maxWager}" inputmode="numeric">
        <button id="submit-final-wager">Lock wager</button>
      ` : `<p>${locked ? "Your team's wager is locked." : `Only ${escapeHtml(leader || "your team leader")} can lock the wager.`}</p>`}
    `;
    if (previousWager !== undefined && $("final-wager-input")) {
      $("final-wager-input").value = previousWager;
    }
    if (previousError) {
      $("final-submit-error").textContent = previousError;
    }
    $("submit-final-wager")?.addEventListener("click", submitFinalWager);
    return;
  }

  if (phase === "answer") {
    action.innerHTML = `
      <h2>${escapeHtml(state.final.category)}</h2>
      <p id="student-final-countdown" class="final-countdown">${countdownText(state.final.answer_expires_at)}</p>
      <div class="student-clue">${escapeHtml(state.final.prompt)}</div>
      <p class="muted">Team leader: ${escapeHtml(leader || "Waiting for leader")}</p>
      <p id="final-submit-error" class="error-text"></p>
      ${isLeader && !submitted ? `
        <label for="final-answer-input">Final answer</label>
        <textarea id="final-answer-input" rows="4"></textarea>
        <button id="submit-final-answer">Submit answer</button>
      ` : `<p>${submitted ? "Your team's answer is submitted." : `Only ${escapeHtml(leader || "your team leader")} can submit the answer.`}</p>`}
    `;
    if (previousAnswer !== undefined && $("final-answer-input")) {
      $("final-answer-input").value = previousAnswer;
    }
    if (previousError) {
      $("final-submit-error").textContent = previousError;
    }
    $("submit-final-answer")?.addEventListener("click", submitFinalAnswer);
    return;
  }

  if (phase === "review") {
    action.innerHTML = `
      <h2>Answers submitted</h2>
      <p>The teacher is reviewing Final Jeopardy.</p>
    `;
    return;
  }

  if (phase === "complete") {
    action.innerHTML = `
      <h2>Final Results</h2>
      <div id="student-winner-stage" class="final-reveal-stage"></div>
    `;
    runFinalReveal("student-winner-stage");
    return;
  }

  action.innerHTML = "";
}

function updateFinalCountdown() {
  const countdown = $("student-final-countdown");
  if (!countdown || !state) {
    return;
  }
  if (state.final.phase === "wager") {
    countdown.textContent = countdownText(state.final.wager_expires_at);
  } else if (state.final.phase === "answer") {
    countdown.textContent = countdownText(state.final.answer_expires_at);
  }
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
        <span>Waiting for the next reveal</span>
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

function renderFinalMode() {
  const inFinal = state.phase.startsWith("final") || state.phase === "complete";
  $("student-final").classList.toggle("hidden", !inFinal);
  $("buzz-button").classList.toggle("hidden", inFinal);
  if (!inFinal) {
    finalActionKey = null;
    return;
  }
  stopMusic();
  const finalOrder = state.final?.reveal_order || [];
  const finalInProgress = state.final?.phase === "complete"
    && state.final?.results_revealed
    && ((state.final.reveal_step || 0) < finalOrder.length || (state.final.reveal_spinning_index ?? null) !== null);
  if (finalInProgress) {
    startResultsMusic();
  } else {
    stopResultsMusic();
  }
  renderFinalLeaderboard();
  renderFinalAction();
}

function canReact(clue) {
  return Boolean(
    state.reactions_enabled
    && clue
    && !clue.is_daily_double
    && state.buzz_winner
    && state.buzz_winner_student
    && !(state.buzz_winner === teamName && state.buzz_winner_student === studentName)
    && !(state.phase.startsWith("final") || state.phase === "complete")
  );
}

function renderReactionControls(clue) {
  const controls = $("reaction-controls");
  controls.classList.add("hidden");
  controls.innerHTML = "";
  if (!canReact(clue)) {
    return;
  }
  const supporting = state.buzz_winner === teamName;
  const emojis = supporting ? supportEmojis : oppositionEmojis;
  controls.classList.remove("hidden");
  controls.innerHTML = `
    <p>${supporting ? "Send some encouragement to your teammate!" : "Send some discouragement to your enemies!"}</p>
    <div class="reaction-buttons">
      ${emojis.map((emoji) => `<button type="button" data-reaction="${emoji}" aria-label="Send ${emoji}">${emoji}</button>`).join("")}
    </div>
  `;
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
  if (!expiresAt || Date.now() / 1000 < expiresAt) {
    return;
  }
  finalAdvanceInFlight = true;
  try {
    const response = await fetch("/api/final/advance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_code: sessionCode }),
    });
    if (response.ok) {
      state = await response.json();
      render();
    }
  } finally {
    finalAdvanceInFlight = false;
  }
}

function render() {
  if (!state) {
    return;
  }
  let team = myTeam();
  const membership = participantMembership();
  if (membership && (membership.team.name !== teamName || !sameIdentity(membership.studentName, studentName))) {
    const previousTeam = teamName;
    teamName = membership.team.name;
    studentName = membership.studentName;
    localStorage.setItem("jeopardy_team", teamName);
    localStorage.setItem("jeopardy_student", studentName);
    $("student-team").textContent = `${studentName} - ${teamName}`;
    if (previousTeam && previousTeam !== teamName) {
      const notice = $("student-move-notice");
      notice.textContent = `Your teacher moved you to ${teamName}.`;
      notice.classList.remove("hidden");
      window.clearTimeout(studentMoveNoticeTimer);
      studentMoveNoticeTimer = window.setTimeout(() => notice.classList.add("hidden"), 6000);
    }
    team = membership.team;
  }
  const matchingCurrentName = team?.students?.some((name) => sameIdentity(name, studentName));
  const storedCurrentParticipant = storedParticipantForStudent(team, studentName);
  const legacyCurrentMembership = matchingCurrentName && !storedCurrentParticipant;
  let stillJoined = Boolean(membership) || legacyCurrentMembership;
  if (!membership && legacyCurrentMembership) {
    claimLegacyMembership();
  }
  if (teamName && studentName && state.teams?.length && !stillJoined) {
    const matchingTeams = state.teams.filter((candidate) => (
      candidate.students?.some((name) => sameIdentity(name, studentName))
    ));
    if (matchingTeams.length === 1) {
      const previousTeam = teamName;
      handleStudentMoved({
        from_team_name: previousTeam,
        to_team_name: matchingTeams[0].name,
        student_name: studentName,
      });
      team = matchingTeams[0];
      stillJoined = true;
    }
  }
  if (teamName && studentName && state.teams?.length && !stillJoined) {
    localStorage.removeItem("jeopardy_session");
    localStorage.removeItem("jeopardy_team");
    $("join-error").textContent = "You are no longer joined to that game.";
    showJoin();
    return;
  }
  const clue = currentClue();
  const button = $("buzz-button");
  const isActiveResponder = amActiveResponder();
  $("buzzer-card").classList.toggle("buzzer-card-active", isActiveResponder);
  $("responder-banner").classList.toggle("hidden", !isActiveResponder);
  const inFinal = state.phase.startsWith("final") || state.phase === "complete";
  if (inFinal) {
    $("student-clue").textContent = "Final Jeopardy";
    button.disabled = true;
  } else if (!clue) {
    $("student-clue").textContent = state.phase === "final"
      ? `${state.final.category}: ${state.final.revealed ? state.final.prompt : "Waiting for Final Jeopardy."}`
      : "Waiting for the teacher to select a clue.";
    button.disabled = true;
    stopMusic();
  } else if (clue.is_daily_double) {
    const isDailyDoubleTeam = state.daily_double.team_name === teamName;
    if (state.daily_double.question_revealed && isDailyDoubleTeam) {
      $("student-clue").innerHTML = clueContentMarkup(clue);
    } else {
      $("student-clue").textContent = isDailyDoubleTeam
        ? "Daily Double. Your leader will lock the wager."
        : "Daily Double.";
    }
    button.disabled = true;
    if (state.daily_double.question_revealed) {
      stopMusic();
    } else {
      startMusic();
    }
  } else {
    $("student-clue").innerHTML = clueContentMarkup(clue);
    button.disabled = !canBuzz(clue);
    if (state.buzz_winner) {
      stopMusic();
    } else {
      startMusic();
    }
  }

  const causedFinalEligibleSelection = Boolean(
    state.buzz_selection_reason === "final_eligible"
    && state.final_eligible_trigger_team === teamName
    && sameIdentity(state.final_eligible_trigger_student, studentName)
  );
  const penalizedDuringFinalEligibleSelection = Boolean(
    state.buzz_selection_reason === "final_eligible" && amEarlyBuzzLocked()
  );
  if (causedFinalEligibleSelection) {
    $("student-status").textContent = `Early buzz penalty awarded. Your early buzz left only one eligible student, so ${state.buzz_winner_student || "the final eligible student"} was automatically selected to answer this question.`;
  } else if (penalizedDuringFinalEligibleSelection) {
    $("student-status").textContent = `Early buzz penalty awarded. You are locked out of this clue, and ${state.buzz_winner_student || "the final eligible student"} was automatically selected after only one eligible student remained.`;
  } else if (state.buzz_winner === teamName) {
    if (state.buzz_winner_student === studentName && state.buzz_selection_reason === "final_eligible") {
      $("student-status").textContent = "You are the final eligible buzzer for this clue, you have been selected to answer this question.";
    } else {
      $("student-status").textContent = state.buzz_winner_student === studentName
        ? "You are up. Give your answer."
        : `${state.buzz_winner_student || "A teammate"} buzzed first.`;
    }
  } else if (state.buzz_winner) {
    $("student-status").textContent = `${state.buzz_winner_student || "A student"} from ${state.buzz_winner} buzzed first.`;
  } else if (clue && state.steal_active && state.steal_lockout_team === teamName) {
    $("student-status").textContent = "Your team is locked out of this steal.";
  } else if (clue && state.steal_active) {
    $("student-status").textContent = "Steal opportunity. Buzz when ready.";
  } else if (clue && amEarlyBuzzLocked()) {
    $("student-status").textContent = "Early buzz penalty awarded; you have been locked out from answering this question. Please do not spam the buzzer and allow the 5 second timer to finish.";
  } else if (clue && !clue.is_daily_double && pendingGlobalRotationTeams(myTeam()).length) {
    $("student-status").textContent = "Everyone on your team has buzzed in.";
  } else if (clue && !clue.is_daily_double && !canBuzz(clue)) {
    $("student-status").textContent = "Wait for the rest of your team to answer.";
  } else if (clue && !clue.is_daily_double) {
    $("student-status").textContent = "Buzz when ready.";
  } else {
    $("student-status").textContent = "";
  }
  $("student-status").classList.toggle("student-status-active", isActiveResponder);
  $("student-status").classList.toggle(
    "student-status-penalty",
    causedFinalEligibleSelection || penalizedDuringFinalEligibleSelection,
  );

  const scores = $("student-scores");
  scores.innerHTML = "";
  state.teams.forEach((team) => {
    const score = displayScore(team);
    const row = document.createElement("div");
    row.className = "score-row";
    if (score < 0) {
      row.classList.add("score-negative");
    }
    row.innerHTML = `<span>${team.name}</span><strong>$${score}</strong>`;
    scores.appendChild(row);
  });

  renderTimer();
  renderScoreEvent();
  renderDailyDoubleAction(clue);
  renderOpeningCountdown();
  renderRemainingTeammates(clue);
  renderReactionControls(clue);
  renderFinalMode();
}

async function submitDailyDoubleWager() {
  const input = $("daily-double-wager-input");
  const error = $("daily-double-error");
  const wager = Number(input.value || 0);
  if (!Number.isInteger(wager) || wager < 0) {
    error.textContent = "Enter a non-negative whole dollar amount.";
    input.focus();
    return;
  }
  const response = await fetch("/api/daily-double/student-wager", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      wager,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    error.textContent = data.detail || "Unable to submit wager.";
    return;
  }
  state = data;
  render();
}

async function submitDailyDoubleAnswer() {
  const input = $("daily-double-answer-input");
  const error = $("daily-double-error");
  const answer = input.value.trim();
  if (!answer) {
    error.textContent = "Enter an answer before submitting.";
    input.focus();
    return;
  }
  const response = await fetch("/api/daily-double/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      answer,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    error.textContent = data.detail || "Unable to submit answer.";
    return;
  }
  state = data;
  render();
}

async function submitFinalWager() {
  const input = $("final-wager-input");
  const error = $("final-submit-error");
  const wager = Number(input.value || 0);
  if (!Number.isInteger(wager) || wager < 0) {
    error.textContent = "Enter a non-negative whole dollar amount.";
    input.focus();
    return;
  }
  if (!window.confirm(`Lock $${wager} as your team's Final Jeopardy wager?`)) {
    return;
  }
  const response = await fetch("/api/final/wager", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      wager,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    error.textContent = data.detail || "Unable to submit wager.";
    input.focus();
    return;
  }
  state = data;
  render();
}

async function submitFinalAnswer() {
  const input = $("final-answer-input");
  const error = $("final-submit-error");
  const answer = input.value.trim();
  if (!answer) {
    error.textContent = "Enter an answer before submitting.";
    input.focus();
    return;
  }
  if (!window.confirm("Submit this as your team's final answer?")) {
    return;
  }
  const response = await fetch("/api/final/answer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      answer,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    error.textContent = data.detail || "Unable to submit answer.";
    input.focus();
    return;
  }
  state = data;
  render();
}

$("join-code").value = sessionCode;
$("student-name").value = studentName;
loadTeamsForCode();
$("join-code").addEventListener("change", loadTeamsForCode);
$("join-code").addEventListener("blur", loadTeamsForCode);

$("join-game").addEventListener("click", async () => {
  sessionCode = $("join-code").value.trim().toUpperCase();
  teamName = $("team-name").value.trim();
  studentName = $("student-name").value.trim();
  $("join-error").textContent = "";
  const response = await fetch("/api/join", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      participant_id: participantId,
    }),
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
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      participant_id: participantId,
    }),
  });
  const data = await response.json();
  if (!response.ok) {
    $("student-status").textContent = data.detail || "Buzz did not register.";
    $("buzz-button").disabled = false;
    return;
  }
  state = data;
  render();
});

$("change-game").addEventListener("click", async () => {
  const previousSessionCode = sessionCode;
  const previousTeamName = teamName;
  const previousStudentName = studentName;
  if (previousSessionCode && previousTeamName && previousStudentName) {
    try {
      await fetch("/api/leave", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        keepalive: true,
        body: JSON.stringify({
          session_code: previousSessionCode,
          team_name: previousTeamName,
          student_name: previousStudentName,
          participant_id: participantId,
        }),
      });
    } catch (_error) {
      // A subsequent join also removes this browser's stale membership.
    }
  }
  localStorage.removeItem("jeopardy_session");
  localStorage.removeItem("jeopardy_team");
  sessionCode = "";
  teamName = "";
  showJoin();
});

$("reaction-controls").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-reaction]");
  if (!button) {
    return;
  }
  button.disabled = true;
  const emoji = button.dataset.reaction;
  const response = await fetch("/api/reactions/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      session_code: sessionCode,
      team_name: teamName,
      student_name: studentName,
      emoji,
    }),
  });
  if (!response.ok && response.status !== 429) {
    const data = await response.json();
    $("student-status").textContent = data.detail || "Reaction did not send.";
  }
  if (response.ok) {
    spawnReaction(emoji, teamName === state?.buzz_winner ? "support" : "opposition");
  }
  window.setTimeout(() => {
    button.disabled = false;
  }, 1500);
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
  timerInterval = window.setInterval(() => {
    renderTimer();
    renderOpeningCountdown();
    if (state?.phase?.startsWith("final")) {
      updateFinalCountdown();
      advanceFinalIfExpired();
    }
  }, 250);
}
