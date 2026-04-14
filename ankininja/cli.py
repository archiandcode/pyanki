from __future__ import annotations

import argparse
import collections
import curses
import json
import random
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass
class Card:
    front: str
    back: str


@dataclass
class DeckEntry:
    path: Path
    is_dir: bool
    card_count: int = 0


@dataclass(frozen=True)
class DifficultyOption:
    key: str
    label: str
    gap: int | None


DIFFICULTIES = [
    DifficultyOption("easy", "Легко", None),
    DifficultyOption("normal", "Нормально", 6),
    DifficultyOption("hard", "Сложно", 3),
    DifficultyOption("very_hard", "Очень сложно", 1),
]

PAIR_DIFFICULTY_EASY = 1
PAIR_DIFFICULTY_NORMAL = 2
PAIR_DIFFICULTY_HARD = 3
PAIR_DIFFICULTY_VERY_HARD = 4
PAIR_NOTES = 5


class StudyState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.path.exists():
            return {"stats": {}, "notes": {}}
        with self.path.open("r", encoding="utf-8") as fh:
            raw = json.load(fh)
        if "stats" in raw or "notes" in raw:
            raw.setdefault("stats", {})
            raw.setdefault("notes", {})
            return raw
        return {"stats": raw, "notes": {}}

    def save(self) -> None:
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)

    def get(self, rel_path: str) -> dict[str, object]:
        stats = self.data.setdefault("stats", {})
        return stats.get(rel_path, {"study_count": 0, "last_opened": None})

    def record_open(self, rel_path: str) -> None:
        entry = self.get(rel_path)
        entry["study_count"] = int(entry.get("study_count", 0)) + 1
        entry["last_opened"] = datetime.now().strftime(TIMESTAMP_FORMAT)
        self.data.setdefault("stats", {})[rel_path] = entry
        self.save()

    def note_key(self, rel_path: str, index: int) -> str:
        return f"{rel_path}::{index}"

    def get_note(self, rel_path: str, index: int) -> str:
        return str(self.data.setdefault("notes", {}).get(self.note_key(rel_path, index), ""))

    def save_note(self, rel_path: str, index: int, note: str) -> None:
        self.data.setdefault("notes", {})[self.note_key(rel_path, index)] = note
        self.save()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ankininja",
        description="Terminal study app for card files stored under ./cards.",
    )
    parser.add_argument(
        "--cards-root",
        type=Path,
        default=Path("cards"),
        help="Root directory with card files.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=Path.home() / ".ankininja" / "study_state.json",
        help="Path to state JSON with study counters.",
    )
    return parser


def parse_cards(deck_file: Path) -> list[Card]:
    cards: list[Card] = []
    with deck_file.open("r", encoding="utf-8") as fh:
        for raw_line in fh:
            line = raw_line.strip()
            if not line:
                continue
            if "|" not in line:
                continue
            front, back = line.split("|", 1)
            cards.append(Card(front=front.strip(), back=back.strip()))
    return cards


def list_entries(current_dir: Path) -> list[DeckEntry]:
    entries: list[DeckEntry] = []
    for path in sorted(current_dir.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            entries.append(DeckEntry(path=path, is_dir=True))
            continue
        if path.is_file():
            try:
                card_count = len(parse_cards(path))
            except OSError:
                card_count = 0
            entries.append(DeckEntry(path=path, is_dir=False, card_count=card_count))
    return entries


def trim_text(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width == 1:
        return value[:1]
    return value[: width - 1] + "…"


def wrap_lines(text: str, width: int) -> list[str]:
    chunks = textwrap.wrap(text, width=max(10, width)) or [text]
    return chunks


def normalize_note_lines(note: str) -> list[str]:
    return note.split("\n") if note else [""]


def visible_note_lines(note: str, width: int) -> list[str]:
    rendered: list[str] = []
    for line in normalize_note_lines(note):
        rendered.extend(split_preserving_spaces(line, width) if line else [""])
    return rendered or [""]


def split_preserving_spaces(text: str, width: int) -> list[str]:
    safe_width = max(1, width)
    if text == "":
        return [""]
    return [text[index : index + safe_width] for index in range(0, len(text), safe_width)]


def is_printable_key(key: object) -> bool:
    return isinstance(key, str) and key.isprintable() and key not in ("\n", "\r", "\t")


def is_enter_key(key: object) -> bool:
    return key in ("\n", "\r", curses.KEY_ENTER)


def is_backspace_key(key: object) -> bool:
    return key in (curses.KEY_BACKSPACE, "\b", "\x7f")


def is_escape_key(key: object) -> bool:
    return key == "\x1b"


class App:
    def __init__(self, cards_root: Path, state: StudyState) -> None:
        self.cards_root = cards_root
        self.state = state
        self.current_dir = cards_root
        self.cursor = 0
        self.scroll = 0
        self.history: list[tuple[Path, int, int]] = []

    def run(self, stdscr: curses.window) -> int:
        curses.curs_set(0)
        stdscr.keypad(True)
        curses.use_default_colors()
        self.setup_colors()

        if not self.cards_root.exists():
            self.show_message(stdscr, f"Не найдена папка: {self.cards_root}")
            return 1
        if not self.cards_root.is_dir():
            self.show_message(stdscr, f"Это не папка: {self.cards_root}")
            return 1

        while True:
            entries = list_entries(self.current_dir)
            action = self.render_browser(stdscr, entries)
            if action == "quit":
                return 0
            if action == "back":
                self.go_back()
                continue
            if not entries:
                continue
            selected = entries[self.cursor]
            if selected.is_dir:
                self.enter_dir(selected.path)
                continue
            should_quit = self.open_deck(stdscr, selected.path)
            if should_quit:
                return 0

    def visible_path(self) -> str:
        rel = self.current_dir.relative_to(self.cards_root)
        return "/" if rel == Path(".") else f"/{rel.as_posix()}"

    def enter_dir(self, path: Path) -> None:
        self.history.append((self.current_dir, self.cursor, self.scroll))
        self.current_dir = path
        self.cursor = 0
        self.scroll = 0

    def go_back(self) -> None:
        if self.history:
            self.current_dir, self.cursor, self.scroll = self.history.pop()

    def open_deck(self, stdscr: curses.window, deck_path: Path) -> bool:
        cards = parse_cards(deck_path)
        if not cards:
            self.show_message(stdscr, f"В файле нет карточек: {deck_path.name}")
            return False
        rel_key = deck_path.relative_to(self.cards_root).as_posix()
        self.state.record_open(rel_key)
        return self.study_deck(stdscr, deck_path, cards)

    def render_browser(self, stdscr: curses.window, entries: list[DeckEntry]) -> str | None:
        while True:
            height, width = stdscr.getmaxyx()
            stdscr.erase()
            title = f"AnkiNinja  cards:{self.visible_path()}"
            help_line = "↑↓ выбор  Enter открыть  ←/Backspace назад  q выход"
            stdscr.addnstr(0, 0, title, width - 1)
            stdscr.addnstr(1, 0, help_line, width - 1)

            if not entries:
                stdscr.addnstr(3, 0, "Папка пустая.", width - 1)
            else:
                self.cursor = max(0, min(self.cursor, len(entries) - 1))
                viewport_height = max(1, height - 4)
                self.ensure_visible(viewport_height)
                start = self.scroll
                end = min(len(entries), start + viewport_height)
                for row, entry in enumerate(entries[start:end], start=3):
                    selected = start + row - 3 == self.cursor
                    if selected:
                        stdscr.attron(curses.A_REVERSE)
                    line = self.format_entry(entry, width)
                    stdscr.addnstr(row, 0, line, width - 1)
                    if selected:
                        stdscr.attroff(curses.A_REVERSE)

            stdscr.refresh()
            key = stdscr.get_wch()
            if key in ("q", "Q"):
                return "quit"
            if key in (curses.KEY_UP, "k") and entries:
                self.cursor = max(0, self.cursor - 1)
            elif key in (curses.KEY_DOWN, "j") and entries:
                self.cursor = min(len(entries) - 1, self.cursor + 1)
            elif key in (curses.KEY_LEFT, curses.KEY_BACKSPACE, "\x7f"):
                return "back"
            elif is_enter_key(key):
                return "open"

    def ensure_visible(self, viewport_height: int) -> None:
        if self.cursor < self.scroll:
            self.scroll = self.cursor
        elif self.cursor >= self.scroll + viewport_height:
            self.scroll = self.cursor - viewport_height + 1

    def format_entry(self, entry: DeckEntry, width: int) -> str:
        name = entry.path.name + ("/" if entry.is_dir else "")
        if entry.is_dir:
            line = f"{trim_text(name, max(10, width - 2))}"
            return line
        rel = entry.path.relative_to(self.cards_root).as_posix()
        state = self.state.get(rel)
        studied = str(state.get("study_count", 0))
        last_opened = str(state.get("last_opened") or "-")
        cards = str(entry.card_count)
        base = f"{name:<32.32} cards:{cards:<4} studied:{studied:<4} last:{last_opened}"
        return trim_text(base, width - 1)

    def study_deck(self, stdscr: curses.window, deck_path: Path, cards: list[Card]) -> bool:
        order = list(range(len(cards)))
        random.shuffle(order)
        queue: collections.deque[int] = collections.deque(order)
        pending: list[tuple[int, int]] = []
        completed: set[int] = set()
        steps_done = 0
        current_index = queue.popleft()
        show_back = False
        rating_index = 0
        rel = deck_path.relative_to(self.cards_root).as_posix()

        while True:
            note = self.state.get_note(rel, current_index)
            height, width = stdscr.getmaxyx()
            stdscr.erase()
            header = f"{rel}  done:{len(completed)}/{len(cards)}  queue:{len(queue)} pending:{len(pending)}"
            if show_back:
                help_line = "←→ сложность  Enter подтвердить  Ctrl+A очистить заметки  Ctrl+B назад  Ctrl+X выход"
            else:
                help_line = "Enter показать ответ  печать=заметки  Ctrl+A очистить  Ctrl+B назад  Ctrl+X выход"
            stdscr.addnstr(0, 0, header, width - 1)
            stdscr.addnstr(1, 0, help_line, width - 1)

            current = cards[current_index]
            front_lines = wrap_lines(f"FRONT: {current.front}", width - 2)
            row = 3
            notes_height = min(6, max(3, height // 4))
            content_limit = max(3, height - notes_height - 2)
            for line in front_lines:
                if row >= content_limit:
                    break
                stdscr.addnstr(row, 0, line, width - 1)
                row += 1

            if show_back and row < content_limit:
                row += 1
                back_lines = wrap_lines(f"BACK: {current.back}", width - 2)
                for line in back_lines:
                    if row >= content_limit:
                        break
                    stdscr.addnstr(row, 0, line, width - 1)
                    row += 1
                if row < content_limit:
                    row += 1
                    self.render_difficulty_line(stdscr, row, rating_index, width)
                    row += 1

            notes_top = max(row + 1, height - notes_height)
            if notes_top < height:
                stdscr.hline(notes_top, 0, "-", max(0, width - 1))
            label = "Notes"
            if notes_top + 1 < height:
                stdscr.addnstr(notes_top + 1, 0, trim_text(label, width - 1), width - 1)
            note_lines = visible_note_lines(note, width - 2)
            visible_lines = max(1, height - notes_top - 2)
            display_lines = note_lines[-visible_lines:]
            note_row = notes_top + 2
            for line in display_lines:
                if note_row >= height:
                    break
                stdscr.addnstr(note_row, 0, " " * max(0, width - 1), width - 1, self.notes_attr)
                stdscr.addnstr(note_row, 0, trim_text(line, width - 1), width - 1, self.notes_attr)
                note_row += 1

            curses.curs_set(1)
            cursor_line = len(display_lines) - 1
            cursor_y = min(height - 1, notes_top + 2 + max(0, cursor_line))
            cursor_x = min(width - 1, len(display_lines[-1]) if display_lines else 0)
            stdscr.move(cursor_y, cursor_x)

            stdscr.refresh()
            key = stdscr.get_wch()
            if key == "\x18":
                return True
            if key == "\x02":
                return False
            changed = self.handle_note_input(key, note, rel, current_index)
            if changed is not None:
                continue
            if not show_back and is_enter_key(key):
                show_back = not show_back
                rating_index = 0
                continue
            if show_back and key == curses.KEY_LEFT:
                rating_index = max(0, rating_index - 1)
                continue
            if show_back and key == curses.KEY_RIGHT:
                rating_index = min(len(DIFFICULTIES) - 1, rating_index + 1)
                continue
            if show_back and is_enter_key(key):
                option = DIFFICULTIES[rating_index]
                steps_done += 1
                if option.gap is None:
                    completed.add(current_index)
                else:
                    pending.append((steps_done + option.gap, current_index))
                self.release_pending(queue, pending, steps_done)
                if not queue and pending:
                    pending.sort(key=lambda item: item[0])
                    queue.append(pending.pop(0)[1])
                if not queue and not pending:
                    self.show_message(stdscr, f"Сессия завершена. Все {len(cards)} карточек отмечены как легко.")
                    return False
                current_index = queue.popleft()
                show_back = False
                rating_index = 0

    def handle_note_input(self, key: object, note: str, rel_path: str, index: int) -> str | None:
        if key == "\x01":
            note = ""
            self.state.save_note(rel_path, index, note)
            return note
        if key == "\x0e":
            note += "\n"
            self.state.save_note(rel_path, index, note)
            return note
        if is_backspace_key(key):
            note = note[:-1]
            self.state.save_note(rel_path, index, note)
            return note
        if is_printable_key(key):
            note += str(key)
            self.state.save_note(rel_path, index, note)
            return note
        return None

    def release_pending(
        self,
        queue: collections.deque[int],
        pending: list[tuple[int, int]],
        steps_done: int,
    ) -> None:
        ready = [item for item in pending if item[0] <= steps_done]
        pending[:] = [item for item in pending if item[0] > steps_done]
        random.shuffle(ready)
        for _, card_index in ready:
            queue.append(card_index)

    def format_difficulty_line(self, rating_index: int, width: int) -> str:
        parts: list[str] = []
        for index, option in enumerate(DIFFICULTIES):
            label = option.label
            if index == rating_index:
                label = f"[{label}]"
            parts.append(label)
        return trim_text("  ".join(parts), width - 1)

    def setup_colors(self) -> None:
        self.difficulty_attrs = {
            0: curses.A_BOLD,
            1: curses.A_BOLD,
            2: curses.A_BOLD,
            3: curses.A_BOLD,
        }
        self.notes_attr = curses.A_UNDERLINE
        if not curses.has_colors():
            return
        curses.start_color()
        curses.init_pair(PAIR_DIFFICULTY_EASY, curses.COLOR_GREEN, -1)
        curses.init_pair(PAIR_DIFFICULTY_NORMAL, curses.COLOR_YELLOW, -1)
        curses.init_pair(PAIR_DIFFICULTY_HARD, curses.COLOR_MAGENTA, -1)
        curses.init_pair(PAIR_DIFFICULTY_VERY_HARD, curses.COLOR_RED, -1)
        curses.init_pair(PAIR_NOTES, -1, curses.COLOR_BLUE)
        self.difficulty_attrs = {
            0: curses.color_pair(PAIR_DIFFICULTY_EASY) | curses.A_BOLD,
            1: curses.color_pair(PAIR_DIFFICULTY_NORMAL) | curses.A_BOLD,
            2: curses.color_pair(PAIR_DIFFICULTY_HARD) | curses.A_BOLD,
            3: curses.color_pair(PAIR_DIFFICULTY_VERY_HARD) | curses.A_BOLD,
        }
        self.notes_attr = curses.color_pair(PAIR_NOTES)

    def render_difficulty_line(
        self,
        stdscr: curses.window,
        row: int,
        rating_index: int,
        width: int,
    ) -> None:
        x = 0
        max_width = max(0, width - 1)
        for index, option in enumerate(DIFFICULTIES):
            label = option.label
            if index == rating_index:
                label = f"[{label.upper()}]"
            if index > 0:
                spacer = "  "
                if x < max_width:
                    stdscr.addnstr(row, x, spacer, max_width - x)
                    x += len(spacer)
            attr = self.difficulty_attrs.get(index, 0)
            if index == rating_index:
                if x < max_width:
                    stdscr.addnstr(row, x, " " * len(label), max_width - x, attr | curses.A_REVERSE | curses.A_STANDOUT)
                    stdscr.addnstr(row, x, label, max_width - x, curses.A_BOLD)
                    x += len(label)
            else:
                if x < max_width:
                    stdscr.addnstr(row, x, label, max_width - x, attr)
                    x += len(label)

    def show_message(self, stdscr: curses.window, message: str) -> None:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        last_row = 0
        for row, line in enumerate(wrap_lines(message, width - 2), start=0):
            if row >= height - 1:
                break
            stdscr.addnstr(row, 0, line, width - 1)
            last_row = row
        prompt = "Нажми любую клавишу."
        if height > 2:
            stdscr.addnstr(min(height - 1, 2 + last_row), 0, prompt, width - 1)
        stdscr.refresh()
        stdscr.getch()


def run_curses(cards_root: Path, state_path: Path) -> int:
    state = StudyState(state_path)
    app = App(cards_root=cards_root, state=state)

    def wrapped(stdscr: curses.window) -> int:
        return app.run(stdscr)

    return curses.wrapper(wrapped)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_curses(args.cards_root.resolve(), args.state)
