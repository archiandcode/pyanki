const GAP_BY_RATING = [null, 6, 3, 1];

const els = {
  setupView: document.querySelector("#setupView"),
  studyView: document.querySelector("#studyView"),
  finishedView: document.querySelector("#finishedView"),
  fileInput: document.querySelector("#fileInput"),
  deckList: document.querySelector("#deckList"),
  deckText: document.querySelector("#deckText"),
  startBtn: document.querySelector("#startBtn"),
  clearBtn: document.querySelector("#clearBtn"),
  setupStatus: document.querySelector("#setupStatus"),
  backBtn: document.querySelector("#backBtn"),
  restartBtn: document.querySelector("#restartBtn"),
  doneStat: document.querySelector("#doneStat"),
  queueStat: document.querySelector("#queueStat"),
  sideLabel: document.querySelector("#sideLabel"),
  frontText: document.querySelector("#frontText"),
  answerBlock: document.querySelector("#answerBlock"),
  backText: document.querySelector("#backText"),
  noteLabel: document.querySelector("#noteLabel"),
  noteInput: document.querySelector("#noteInput"),
  showBtn: document.querySelector("#showBtn"),
  difficultyButtons: document.querySelector("#difficultyButtons"),
  finishedCount: document.querySelector("#finishedCount"),
  againBtn: document.querySelector("#againBtn"),
  newDeckBtn: document.querySelector("#newDeckBtn"),
};

const state = {
  cards: [],
  queue: [],
  pending: [],
  completed: new Set(),
  stepsDone: 0,
  currentIndex: null,
  showBack: false,
};

function parseDeck(text) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const splitAt = line.indexOf("|");
      if (splitAt === -1) return null;
      const front = line.slice(0, splitAt).trim();
      const back = line.slice(splitAt + 1).trim();
      if (!front || !back) return null;
      return { front, back };
    })
    .filter(Boolean);
}

async function discoverDecks(path = "cards/", seen = new Set()) {
  if (seen.has(path)) return [];
  seen.add(path);

  const response = await fetch(path);
  if (!response.ok) return [];

  const html = await response.text();
  const doc = new DOMParser().parseFromString(html, "text/html");
  const links = [...doc.querySelectorAll("a[href]")]
    .map((link) => new URL(link.getAttribute("href"), response.url))
    .filter((url) => url.origin === location.origin)
    .map((url) => url.pathname.replace(/^\/+/, ""))
    .filter((href) => href.startsWith("cards/") && !href.includes("?"));

  const decks = [];
  for (const href of links) {
    if (href.endsWith("/")) {
      decks.push(...await discoverDecks(href, seen));
    } else if (href.toLowerCase().endsWith(".txt")) {
      decks.push(href);
    }
  }

  return [...new Set(decks)].sort((left, right) => left.localeCompare(right));
}

async function loadDeckFromPath(path) {
  const response = await fetch(path);
  if (!response.ok) {
    els.setupStatus.textContent = `Не удалось открыть ${path}`;
    return;
  }
  els.deckText.value = await response.text();
  const count = parseDeck(els.deckText.value).length;
  els.setupStatus.textContent = `${path}: карточек ${count}`;
}

async function renderDeckList() {
  try {
    const decks = await discoverDecks();
    els.deckList.replaceChildren();

    const title = document.createElement("div");
    title.className = "deck-list-title";
    title.textContent = decks.length > 0 ? "Колоды из cards/" : "В cards/ не найдено .txt файлов";
    els.deckList.append(title);

    for (const deck of decks) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = deck;
      button.addEventListener("click", () => loadDeckFromPath(deck));
      els.deckList.append(button);
    }
  } catch {
    els.deckList.querySelector(".deck-list-title").textContent = "Автопоиск cards/ работает только через локальный сервер";
  }
}

function shuffle(items) {
  const copy = [...items];
  for (let index = copy.length - 1; index > 0; index -= 1) {
    const swapIndex = Math.floor(Math.random() * (index + 1));
    [copy[index], copy[swapIndex]] = [copy[swapIndex], copy[index]];
  }
  return copy;
}

function setView(name) {
  els.setupView.classList.toggle("hidden", name !== "setup");
  els.studyView.classList.toggle("hidden", name !== "study");
  els.finishedView.classList.toggle("hidden", name !== "finished");
}

function startSession(cards) {
  state.cards = cards;
  state.queue = shuffle(cards.map((_, index) => index));
  state.pending = [];
  state.completed = new Set();
  state.stepsDone = 0;
  state.currentIndex = state.queue.shift();
  state.showBack = false;
  els.noteInput.value = "";
  setView("study");
  renderStudy();
}

function renderStudy() {
  const card = state.cards[state.currentIndex];
  if (!card) return;

  els.doneStat.textContent = `${state.completed.size}/${state.cards.length}`;
  els.queueStat.textContent = `очередь ${state.queue.length} · повтор ${state.pending.length}`;
  els.sideLabel.textContent = state.showBack ? "Вопрос" : "Вопрос";
  els.frontText.textContent = card.front;
  els.backText.textContent = card.back;

  els.answerBlock.classList.toggle("hidden", !state.showBack);
  els.showBtn.classList.toggle("hidden", state.showBack);
  els.difficultyButtons.classList.toggle("hidden", !state.showBack);

  els.noteInput.disabled = state.showBack;
  els.noteLabel.textContent = state.showBack ? "Заметка (только чтение)" : "Заметка";

}

function showAnswer() {
  state.showBack = true;
  renderStudy();
}

function releasePending() {
  const ready = [];
  const waiting = [];

  for (const item of state.pending) {
    if (item.step <= state.stepsDone) ready.push(item);
    else waiting.push(item);
  }

  state.pending = waiting;
  for (const item of shuffle(ready).reverse()) {
    state.queue.unshift(item.index);
  }
}

function rateCurrent(rating) {
  if (!state.showBack) return;

  const gap = GAP_BY_RATING[rating];
  state.stepsDone += 1;

  if (gap === null) {
    state.completed.add(state.currentIndex);
  } else {
    state.pending.push({
      step: state.stepsDone + gap,
      index: state.currentIndex,
    });
  }

  releasePending();

  if (state.queue.length === 0 && state.pending.length > 0) {
    state.pending.sort((left, right) => left.step - right.step);
    state.queue.push(state.pending.shift().index);
  }

  if (state.queue.length === 0 && state.pending.length === 0) {
    els.finishedCount.textContent = `${state.cards.length} карточек`;
    setView("finished");
    return;
  }

  state.currentIndex = state.queue.shift();
  state.showBack = false;
  els.noteInput.value = "";
  renderStudy();
}

function startFromTextarea() {
  const cards = parseDeck(els.deckText.value);
  if (cards.length === 0) {
    els.setupStatus.textContent = "Не нашел карточки. Нужен формат: вопрос | ответ.";
    return;
  }
  els.setupStatus.textContent = `Загружено карточек: ${cards.length}`;
  startSession(cards);
}

els.fileInput.addEventListener("change", async () => {
  const [file] = els.fileInput.files;
  if (!file) return;
  els.deckText.value = await file.text();
  const count = parseDeck(els.deckText.value).length;
  els.setupStatus.textContent = `Файл загружен. Карточек: ${count}`;
});

els.startBtn.addEventListener("click", startFromTextarea);
els.clearBtn.addEventListener("click", () => {
  els.deckText.value = "";
  els.setupStatus.textContent = "Формат: вопрос | ответ, одна карточка на строку.";
});

els.showBtn.addEventListener("click", showAnswer);
els.difficultyButtons.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-rating]");
  if (!button) return;
  rateCurrent(Number(button.dataset.rating));
});

els.backBtn.addEventListener("click", () => setView("setup"));
els.restartBtn.addEventListener("click", () => startSession(state.cards));
els.againBtn.addEventListener("click", () => startSession(state.cards));
els.newDeckBtn.addEventListener("click", () => setView("setup"));

document.addEventListener("keydown", (event) => {
  if (els.studyView.classList.contains("hidden")) return;
  if (!state.showBack && event.key === "Enter" && event.target !== els.noteInput) {
    event.preventDefault();
    showAnswer();
    return;
  }
  if (state.showBack && ["1", "2", "3", "4"].includes(event.key)) {
    event.preventDefault();
    rateCurrent(Number(event.key) - 1);
  }
});

renderDeckList();
