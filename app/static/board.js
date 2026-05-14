let state = null;
let socket = null;
let lastScoreEventId = null;
let answerRevealUntil = 0;
let revealStartedFor = null;
let audioContext = null;
let hasSeenScoreState = false;
let lastPresentedClueId = null;
let confettiSignature = null;

const $ = (id) => document.getElementById(id);
const audioCacheBust = `v=${Date.now()}`;
const audioUrl = (path) => `${path}?${audioCacheBust}`;

const audioFiles = {
  suspense: new Audio(audioUrl("/static/audio/suspense_reveal.wav")),
  results: new Audio(audioUrl("/static/audio/results_ceremony.wav")),
};
audioFiles.results.loop = true;
audioFiles.suspense.volume = 0.65;
audioFiles.results.volume = 0.315;

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

function celebrationMusic() {
  [392, 523, 659, 784, 1046].forEach((frequency, index) => {
    playTone(frequency, 0.18, "triangle", 0.1, index * 0.14);
  });
}

function drumroll(duration = 1300) {
  audioFiles.suspense.currentTime = 0;
  audioFiles.suspense.play().catch(() => {});
  return;
  const startedAt = Date.now();
  const interval = window.setInterval(() => {
    const elapsed = Date.now() - startedAt;
    playTone(90 + Math.random() * 35, 0.045, "square", 0.055);
    if (elapsed >= duration) {
      window.clearInterval(interval);
    }
  }, 70);
}

function startResultsMusic() {
  audioFiles.results.play().catch(() => {});
}

function stopResultsMusic() {
  audioFiles.results.pause();
}

function ordinal(value) {
  const mod100 = value % 100;
  if (mod100 >= 11 && mod100 <= 13) {
    return `${value}th`;
  }
  const mod10 = value % 10;
  if (mod10 === 1) return `${value}st`;
  if (mod10 === 2) return `${value}nd`;
  if (mod10 === 3) return `${value}rd`;
  return `${value}th`;
}

async function fetchLatest() {
  const response = await fetch("/api/game/latest");
  if (!response.ok) {
    return;
  }
  state = await response.json();
  connectSocket(state.session_code);
  render();
}

async function fetchState(code) {
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
  socket.onmessage = () => fetchState(code);
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

function renderScores() {
  $("board-title").textContent = state.title;
  const scores = $("board-scores");
  const teams = [...state.teams].sort((left, right) => displayScore(right) - displayScore(left));
  scores.innerHTML = teams.map((team, index) => `
    <div class="presentation-score-row">
      <span>${index + 1}</span>
      <strong>${escapeHtml(team.name)}</strong>
      <em>$${displayScore(team)}</em>
    </div>
  `).join("");
}

function renderGameBoard() {
  const board = $("presentation-board");
  const columns = state.categories.length;
  board.style.gridTemplateColumns = `repeat(${columns}, minmax(110px, 1fr))`;
  board.innerHTML = "";

  state.categories.forEach((category) => {
    const header = document.createElement("div");
    header.className = "presentation-category";
    header.textContent = category.name;
    board.appendChild(header);
  });

  const maxRows = Math.max(...state.categories.map((category) => category.clues.length));
  for (let row = 0; row < maxRows; row += 1) {
    state.categories.forEach((category) => {
      const clue = category.clues[row];
      const tile = document.createElement("div");
      tile.className = "presentation-tile";
      if (!clue) {
        tile.classList.add("presentation-tile-empty");
      } else {
        tile.textContent = clue.revealed ? "" : `$${clue.value}`;
        tile.classList.toggle("revealed", clue.revealed);
        tile.classList.toggle("active", state.current_clue_id === clue.id);
      }
      board.appendChild(tile);
    });
  }
}

function renderClue() {
  const clue = currentClue();
  const board = $("board-clue");
  if (state.phase?.startsWith("final")) {
    board.classList.add("presentation-clue-card-open");
    board.innerHTML = `
      <p class="eyebrow">Final Jeopardy</p>
      <h2>${escapeHtml(state.final.category)}</h2>
      <p>${state.final.revealed ? escapeHtml(state.final.prompt) : "Wagers are being locked in."}</p>
    `;
    return;
  }
  if (!clue) {
    if (Date.now() < answerRevealUntil && state.score_event?.clue_answer) {
      board.innerHTML = `
        <p class="eyebrow">$${state.score_event.clue_value || ""}</p>
        <h2>${escapeHtml(state.score_event.clue_prompt || "")}</h2>
        <div class="presentation-answer">${escapeHtml(state.score_event.clue_answer)}</div>
      `;
      return;
    }
    board.classList.remove("presentation-clue-card-open");
    board.textContent = "Waiting for the teacher.";
    lastPresentedClueId = null;
    return;
  }
  const showAnswer = Date.now() < answerRevealUntil && Boolean(clue.answer);
  const dailyDoubleHidden = clue.is_daily_double && !state.daily_double?.question_revealed;
  if (lastPresentedClueId !== clue.id) {
    board.classList.remove("presentation-clue-card-open");
    window.requestAnimationFrame(() => board.classList.add("presentation-clue-card-open"));
    lastPresentedClueId = clue.id;
  } else {
    board.classList.add("presentation-clue-card-open");
  }
  board.innerHTML = `
    <p class="eyebrow">${clue.is_daily_double ? "Daily Double" : `$${clue.value}`}</p>
    <h2>${dailyDoubleHidden ? "Wager pending" : escapeHtml(clue.prompt)}</h2>
    ${showAnswer && !dailyDoubleHidden ? `<div class="presentation-answer">${escapeHtml(clue.answer)}</div>` : ""}
  `;
}

function maybeRevealAnswer() {
  if (!hasSeenScoreState) {
    hasSeenScoreState = true;
    lastScoreEventId = state.score_event?.id ?? null;
    return;
  }
  if (!state.score_event || state.score_event.id === lastScoreEventId) {
    return;
  }
  lastScoreEventId = state.score_event.id;
  if (state.score_event.clue_answer) {
    answerRevealUntil = Date.now() + 5000;
  }
}

function renderWinnerReveal() {
  const reveal = $("winner-reveal");
  if (state.final.phase !== "complete" || !state.final.results_revealed) {
    reveal.classList.add("hidden");
    return;
  }
  reveal.classList.remove("hidden");
  const signature = state.final.reveal_order.join("|") + JSON.stringify(state.final.final_scores || {}) + `:${state.final.reveal_step || 0}:${state.final.reveal_spinning_index ?? null}`;
  if (revealStartedFor === signature) {
    return;
  }
  revealStartedFor = signature;
  runWinnerReveal();
}

function runWinnerReveal() {
  const stage = $("winner-stage");
  stage.innerHTML = "";
  const teams = state.final.reveal_order
    .map((name) => state.teams.find((team) => team.name === name))
    .filter(Boolean);
  const revealStep = state.final.reveal_step || 0;
  const spinningIndex = state.final.reveal_spinning_index ?? null;
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
    revealTeam(stage, team, index, teams.length);
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
    if (onDone && ticks > 24) {
      window.clearInterval(interval);
      onDone?.();
    }
  }, 70);
}

function revealTeam(stage, team, index, total) {
  const winner = index === total - 1;
  const place = total - index;
  const revealStep = state.final.reveal_step || 0;
  const shouldSpin = index === (state.final.reveal_spinning_index ?? null);
  const finalScore = finalScoreFor(team);
  const row = document.createElement("div");
  row.className = `winner-row ${winner ? "winner-row-champion" : ""}`;
  if (!shouldSpin) {
    row.innerHTML = `<small>In ${ordinal(place)} place we have:</small><span>${escapeHtml(team.name)}</span><strong>$${finalScore}</strong>`;
    stage.appendChild(row);
    return;
  }
  row.classList.add("winner-row-revealing");
  row.innerHTML = `<small>In ${ordinal(place)} place we have:</small><span class="winner-name-hidden">????</span><strong data-slot-machine="true">$0000</strong>`;
  stage.appendChild(row);
  startSlotSpin(row.querySelector("[data-slot-machine]"));
}

function finalScoreFor(team) {
  return state.final?.final_scores?.[team.name] ?? team.score;
}

function displayScore(team) {
  return team.score;
}

function confetti() {
  for (let index = 0; index < 80; index += 1) {
    const piece = document.createElement("i");
    piece.className = "confetti-piece";
    piece.style.left = `${Math.random() * 100}%`;
    piece.style.animationDelay = `${Math.random() * 0.5}s`;
    piece.style.background = ["#f2c94c", "#32d583", "#ff6b6b", "#60a5fa"][index % 4];
    document.body.appendChild(piece);
    window.setTimeout(() => piece.remove(), 2600);
  }
}

function render() {
  if (!state) {
    return;
  }
  const finalOrder = state.final?.reveal_order || [];
  const finalInProgress = state.final?.phase === "complete"
    && state.final?.results_revealed
    && ((state.final.reveal_step || 0) < finalOrder.length || (state.final.reveal_spinning_index ?? null) !== null);
  if (finalInProgress) {
    startResultsMusic();
  } else {
    stopResultsMusic();
  }
  const finalDone = state.final?.phase === "complete"
    && state.final?.results_revealed
    && finalOrder.length > 0
    && (state.final.reveal_step || 0) >= finalOrder.length
    && (state.final.reveal_spinning_index ?? null) === null;
  const doneSignature = finalDone ? `${state.session_code}:${finalOrder.join("|")}:${JSON.stringify(state.final.final_scores || {})}` : null;
  if (finalDone && confettiSignature !== doneSignature) {
    confettiSignature = doneSignature;
    celebrationMusic();
    confetti();
  }
  maybeRevealAnswer();
  renderGameBoard();
  renderScores();
  renderClue();
  renderWinnerReveal();
}

window.setInterval(renderClue, 250);
fetchLatest();
