"""Sprava vice repo namespace: aktivni repo, znama repa a jejich soubory.

Kazde repo ma vlastni adresar v data/<owner>__<repo>/ s tickets.txt.
Volba aktivniho repa a seznam znamych repo ziji v app-state.json
v korenu (napric repo, nikoli namespaceovane).
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


def is_valid_repo(name: str) -> bool:
    return bool(REPO_RE.match(name.strip()))


def repo_slug(repo: str) -> str:
    owner, _, name = repo.partition("/")
    return f"{owner}__{name}"


def repo_short_name(repo: str) -> str:
    """The repo name without its owner, e.g. 'DigiLearnDev/DigiLearn' -> 'DigiLearn'."""
    _, _, name = repo.partition("/")
    return name


def repo_dir(repo: str) -> Path:
    return DATA_DIR / repo_slug(repo)


def tickets_path(repo: str) -> Path:
    return repo_dir(repo) / "tickets.txt"


def ensure_repo_files(repo: str) -> None:
    d = repo_dir(repo)
    d.mkdir(parents=True, exist_ok=True)
    tf = tickets_path(repo)
    if not tf.exists():
        tf.write_text("", encoding="utf-8")


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


def toggle_ticket_status(repo: str, ticket_number: int) -> str:
    """Flips a ticket's status and returns the new status. Delegates the
    tickets.txt format entirely to ticket_store."""
    import ticket_store

    return ticket_store.toggle_status(tickets_path(repo), ticket_number)


def switch_active_repo(state: dict, repo: str) -> dict:
    """Adds repo to known_repos if new, makes it active, persists app-state.json."""
    if repo not in state["known_repos"]:
        state["known_repos"].append(repo)
    state["active_repo"] = repo
    ensure_repo_files(repo)
    save_app_state(state)
    return state
