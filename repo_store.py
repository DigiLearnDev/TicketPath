"""Sprava vice repo namespace: aktivni repo, znama repa a jejich soubory.

Kazde repo ma vlastni adresar v data/<owner>__<repo>/ s tickets.txt a
diagram-state.json. Volba aktivniho repa a seznam znamych repo ziji v
app-state.json v korenu (napric repo, nikoli namespaceovane).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
APP_STATE_FILE = HERE / "app-state.json"
LEGACY_TICKETS_FILE = HERE / "tickets.txt"
DEFAULT_REPO = "DigiLearnDev/DigiLearn"

REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

EMPTY_STATE = {"tickets": {}, "phase_dividers": []}


def is_valid_repo(name: str) -> bool:
    return bool(REPO_RE.match(name.strip()))


def repo_slug(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}"


def repo_dir(repo: str) -> Path:
    return DATA_DIR / repo_slug(repo)


def tickets_path(repo: str) -> Path:
    return repo_dir(repo) / "tickets.txt"


def diagram_state_path(repo: str) -> Path:
    return repo_dir(repo) / "diagram-state.json"


def ensure_repo_files(repo: str) -> None:
    d = repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    tf = tickets_path(repo)
    if not tf.exists():
        tf.write_text("", encoding="utf-8")
    sf = diagram_state_path(repo)
    if not sf.exists():
        sf.write_text(json.dumps(EMPTY_STATE, indent=2), encoding="utf-8")


def _migrate_legacy_tickets() -> None:
    """One-time move of the old root tickets.txt into the default repo's namespace."""
    target = tickets_path(DEFAULT_REPO)
    if LEGACY_TICKETS_FILE.exists() and not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(LEGACY_TICKETS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        LEGACY_TICKETS_FILE.unlink()


def load_app_state() -> dict:
    _migrate_legacy_tickets()
    if not APP_STATE_FILE.exists():
        state = {"active_repo": DEFAULT_REPO, "known_repos": [DEFAULT_REPO]}
        ensure_repo_files(DEFAULT_REPO)
        save_app_state(state)
        return state

    state = json.loads(APP_STATE_FILE.read_text(encoding="utf-8"))
    state.setdefault("active_repo", DEFAULT_REPO)
    state.setdefault("known_repos", [DEFAULT_REPO])
    if state["active_repo"] not in state["known_repos"]:
        state["known_repos"].append(state["active_repo"])
    ensure_repo_files(state["active_repo"])
    return state


def save_app_state(state: dict) -> None:
    APP_STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_diagram_state(repo: str) -> dict:
    ensure_repo_files(repo)
    return json.loads(diagram_state_path(repo).read_text(encoding="utf-8"))


def save_diagram_state(repo: str, state: dict) -> None:
    diagram_state_path(repo).write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def set_manual_layer(repo: str, ticket_number: int, layer: int) -> dict:
    state = load_diagram_state(repo)
    state.setdefault("tickets", {})
    key = str(ticket_number)
    entry = state["tickets"].setdefault(key, {"manual_layer": None, "status_override": None})
    entry["manual_layer"] = layer
    save_diagram_state(repo, state)
    return state


TICKET_BLOCK_RE = re.compile(
    r"(^ticket:\s*(\d+)\s*$.*?^status:\s*)(\S+)(\s*$)",
    re.MULTILINE | re.DOTALL,
)


def toggle_ticket_status(repo: str, ticket_number: int) -> str:
    """Flips a ticket's status: line directly in tickets.txt and returns the new status.

    This is a stand-in value until the next GitHub refresh overwrites it with the
    real state of the issue — not a separate override layer.
    """
    path = tickets_path(repo)
    text = path.read_text(encoding="utf-8")

    new_status_holder: list[str] = []

    def _flip(match: re.Match) -> str:
        if int(match.group(2)) != ticket_number:
            return match.group(0)
        current = match.group(3).strip().lower()
        new_status = "open" if current == "closed" else "closed"
        new_status_holder.append(new_status)
        return f"{match.group(1)}{new_status}{match.group(4)}"

    new_text = TICKET_BLOCK_RE.sub(_flip, text)

    if not new_status_holder:
        raise ValueError(f"ticket {ticket_number} not found in {path}")

    path.write_text(new_text, encoding="utf-8")
    return new_status_holder[0]


def switch_active_repo(state: dict, repo: str) -> dict:
    """Adds repo to known_repos if new, makes it active, persists app-state.json."""
    if repo not in state["known_repos"]:
        state["known_repos"].append(repo)
    state["active_repo"] = repo
    ensure_repo_files(repo)
    save_app_state(state)
    return state
