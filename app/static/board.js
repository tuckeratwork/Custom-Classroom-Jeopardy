let state = null;
let socket = null;
let lastScoreEventId = null;
let answerRevealUntil = 0;
let revealStartedFor = null;
let audioContext = null;
let hasSeenScoreState = false;
let lastPresentedClueId = null;
let confettiSignature = null;
let autoRandomAdvanceInFlight = false;
let lastZoomClueId = null;

const BOARD_ZOOM_MIN = 70;
const BOARD_ZOOM_MAX = 140;
const BOARD_ZOOM_STEP = 10;
const BOARD_ZOOM_STORAGE_KEY = "jeopardy-presentation-size";
let boardZoom = Number.parseInt(window.localStorage.getItem(BOARD_ZOOM_STORAGE_KEY), 10);
if (!Number.isFinite(boardZoom) || boardZoom < BOARD_ZOOM_MIN || boardZoom > BOARD_ZOOM_MAX) {
  boardZoom = 100;
}

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

function imageUrl(path) {
  if (!path) {
    return "";
  }
  if (/^(https?:)?\/\//.test(path) || path.startsWith("/")) {
    return path;
  }
  return `/game-assets/${encodeURI(path)}`;
}

function questionImageMarkup(clue) {
  const image = imageUrl(clue?.image);
  return image ? `<img class="question-image presentation-question-image" src="${escapeHtml(image)}" alt="">` : "";
}

function applyBoardZoom() {
  document.documentElement.style.setProperty("--presentation-scale", String(boardZoom / 100));
  $("board-zoom-value").textContent = `${boardZoom}%`;
  $("board-zoom-out").disabled = boardZoom <= BOARD_ZOOM_MIN;
  $("board-zoom-in").disabled = boardZoom >= BOARD_ZOOM_MAX;
  window.localStorage.setItem(BOARD_ZOOM_STORAGE_KEY, String(boardZoom));
}

function changeBoardZoom(delta) {
  boardZoom = Math.min(BOARD_ZOOM_MAX, Math.max(BOARD_ZOOM_MIN, boardZoom + delta));
  applyBoardZoom();
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
  item.style.animationDuration = `${1900 + Math.random() * 900}ms`;
  stream.appendChild(item);
  window.setTimeout(() => item.remove(), 3000);
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
  const params = new URLSearchParams(window.location.search);
  const code = params.get("session");
  if (code) {
    await fetchState(code.toUpperCase());
    if (state?.session_code) {
      connectSocket(state.session_code);
    }
    return;
  }
  const response = await fetch("/api/game/latest", { credentials: "include" });
  if (!response.ok) {
    return;
  }
  state = await response.json();
  connectSocket(state.session_code);
  render();
}

async function fetchState(code) {
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
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.type === "emoji_reaction") {
      spawnReaction(message.emoji, message.side);
      return;
    }
    fetchState(code);
  };
}

function currentClue() {
  if (!state?.current_clue_id) {
    return null;
  }
  return findClueById(state.current_clue_id);
}

function findClueById(clueId) {
  if (!clueId) {
    return null;
  }
  for (const category of state.categories) {
    for (const clue of category.clues) {
      if (clue.id === clueId) {
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
    <div class="presentation-score-row ${displayScore(team) < 0 ? "score-negative" : ""}">
      <span>${index + 1}</span>
      <strong>${escapeHtml(team.name)}</strong>
      <em>$${displayScore(team)}</em>
    </div>
  `).join("");
}

function animateTileZoom(tile) {
  if (!tile) {
    return;
  }
  const start = tile.getBoundingClientRect();
  const inset = 24;
  const end = {
    left: inset,
    top: inset,
    width: window.innerWidth - inset * 2,
    height: window.innerHeight - inset * 2,
  };
  if (!start.width || !end.width || !end.height) {
    return;
  }
  const zoom = document.createElement("div");
  zoom.className = "tile-zoom";
  zoom.textContent = tile.textContent;
  Object.assign(zoom.style, {
    left: `${start.left}px`,
    top: `${start.top}px`,
    width: `${start.width}px`,
    height: `${start.height}px`,
  });
  document.body.appendChild(zoom);
  window.requestAnimationFrame(() => {
    Object.assign(zoom.style, {
      left: `${end.left}px`,
      top: `${end.top}px`,
      width: `${end.width}px`,
      height: `${end.height}px`,
    });
    zoom.classList.add("tile-zoom-expanded");
  });
  window.setTimeout(() => zoom.remove(), 520);
}

function renderGameBoard() {
  const board = $("presentation-board");
  const columns = state.categories.length;
  board.style.gridTemplateColumns = `repeat(${columns}, minmax(calc(110px * var(--presentation-scale)), 1fr))`;
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
        if (state.current_clue_id === clue.id && lastZoomClueId !== clue.id) {
          animateTileZoom(tile);
          lastZoomClueId = clue.id;
        }
      }
      board.appendChild(tile);
    });
  }
}

function renderClue() {
  const clue = currentClue();
  const board = $("board-clue");
  if (state.final?.phase && state.final.phase !== "pending") {
    document.body.classList.remove("presentation-question-active");
    board.classList.add("presentation-clue-card-open");
    const hidden = state.final.phase === "pending" || state.final.phase === "wager";
    board.innerHTML = `
      <p class="eyebrow">Final Jeopardy</p>
      <h2>${hidden ? "Wagers are being locked in." : escapeHtml(state.final.category)}</h2>
      <p>${hidden ? "Category and prompt hidden." : (state.final.revealed ? escapeHtml(state.final.prompt) : "Prompt hidden.")}</p>
    `;
    return;
  }
  if (!clue) {
    document.body.classList.remove("presentation-question-active");
    lastZoomClueId = null;
    const completedClue = findClueById(state.last_completed_clue_id);
    if (completedClue?.answer) {
      board.classList.add("presentation-clue-card-open");
      board.innerHTML = `
        <p class="eyebrow">${completedClue.is_daily_double ? "Daily Double" : `$${completedClue.value}`}</p>
        <h2>${escapeHtml(completedClue.prompt || "")}</h2>
        ${questionImageMarkup(completedClue)}
        <div class="presentation-answer">${escapeHtml(completedClue.answer)}</div>
      `;
      return;
    }
    board.classList.add("presentation-clue-card-open");
    board.textContent = "Waiting for the teacher.";
    lastPresentedClueId = null;
    return;
  }
  document.body.classList.add("presentation-question-active");
  if (lastPresentedClueId !== clue.id) {
    answerRevealUntil = 0;
    board.classList.remove("presentation-clue-card-open");
    window.requestAnimationFrame(() => board.classList.add("presentation-clue-card-open"));
    lastPresentedClueId = clue.id;
  } else {
    board.classList.add("presentation-clue-card-open");
  }
  const showAnswer = Date.now() < answerRevealUntil && Boolean(clue.answer);
  const dailyDoubleHidden = clue.is_daily_double && !state.daily_double?.question_revealed;
  board.innerHTML = `
    <p class="eyebrow">${clue.is_daily_double ? "Daily Double" : `$${clue.value}`}</p>
    <h2>${dailyDoubleHidden ? "Wager pending" : escapeHtml(clue.prompt)}</h2>
    ${!dailyDoubleHidden ? questionImageMarkup(clue) : ""}
    ${buzzWinnerMarkup()}
    ${autoRandomCountdownMarkup()}
    ${showAnswer && !dailyDoubleHidden ? `<div class="presentation-answer">${escapeHtml(clue.answer)}</div>` : ""}
  `;
}

function autoRandomCountdownMarkup() {
  if (!state.auto_random_expires_at || state.buzz_winner) {
    return "";
  }
  const remaining = Math.max(0, Math.ceil(state.auto_random_expires_at - Date.now() / 1000));
  return `<div class="presentation-auto-random">Random buzz in ${remaining}</div>`;
}

function buzzWinnerMarkup() {
  if (!state.buzz_winner) {
    return "";
  }
  return `
    <div class="presentation-buzz-winner">
      <span>${escapeHtml(state.buzz_winner_student || "Student")}</span>
      <strong>${escapeHtml(state.buzz_winner)}</strong>
    </div>
  `;
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
    const response = await fetch("/api/buzzer/auto-random/advance", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_code: state.session_code }),
    });
    if (response.ok) {
      state = await response.json();
      render();
    }
  } finally {
    autoRandomAdvanceInFlight = false;
  }
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
  if (finalScore < 0) {
    row.classList.add("score-negative");
  }
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

window.setInterval(() => {
  renderClue();
  advanceAutoRandomIfExpired();
}, 250);

$("board-zoom-out").addEventListener("click", () => changeBoardZoom(-BOARD_ZOOM_STEP));
$("board-zoom-reset").addEventListener("click", () => {
  boardZoom = 100;
  applyBoardZoom();
});
$("board-zoom-in").addEventListener("click", () => changeBoardZoom(BOARD_ZOOM_STEP));

applyBoardZoom();
fetchLatest();
