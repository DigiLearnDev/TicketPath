"""Servíruje diagram ze stavu ticketu v tickets.txt na http://localhost:8765.

Pouziti:
    python server.py

Zadne externi zavislosti (jen standardni knihovna), zadne sitove pozadavky —
diagram je pri kazdem pozadavku prepocitan z tickets.txt aktivniho repa stejne
jako v generate_diagram.py. Hlavicka nabizi prepinac aktivniho repa (viz
repo_store.py) — kazde repo ma vlastni namespaceovane soubory, prepnuti nikdy
necte ani neprepisuje data jineho repa.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

import github_refresh
import repo_store
import ticket_store
from generate_diagram import build_html
from ticket import Ticket

HOST = "localhost"
PORT = 8765

# In-memory only, per the spec: the "new ticket" badge has no dismiss state to
# persist — it is only ever known for the lifetime of this server process and
# is overwritten wholesale by the next refresh. Never written to disk.
_new_tickets_by_repo: dict[str, set[int]] = {}


def refresh_repo(repo: str) -> tuple[list[Ticket], set[int]]:
    """Orchestrates a refresh: downloads via github_refresh, diffs against the
    tickets already on disk, then saves. github_refresh only fetches and
    translates — this sequencing (and the disk access) lives here so it can
    be tested with a faked fetch, without calling `gh`.
    """
    repo_store.ensure_repo_files(repo)
    path = repo_store.tickets_path(repo)
    old_text = path.read_text(encoding="utf-8") if path.exists() else ""
    old_tickets = ticket_store.parse_tickets(old_text) if old_text else []
    old_numbers = {t.number for t in old_tickets}

    raw_issues = github_refresh.fetch_all_issues(repo)
    tickets = github_refresh.build_tickets_from_issues(raw_issues)
    new_numbers = github_refresh.diff_new_tickets(old_numbers, {t.number for t in tickets})

    header = ticket_store.split_header(old_text)
    ticket_store.save_tickets(path, tickets, header)

    return tickets, new_numbers


class DiagramHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/api/repos":
            state = repo_store.load_app_state()
            self._send_json(200, state)
            return

        state = repo_store.load_app_state()
        tickets = ticket_store.load_tickets(repo_store.tickets_path(state["active_repo"]))
        new_tickets = _new_tickets_by_repo.get(state["active_repo"], set())
        body = build_html(
            tickets,
            new_tickets=new_tickets,
            repo_short_name=repo_store.repo_short_name(state["active_repo"]),
            known_repos=state["known_repos"],
            active_repo=state["active_repo"],
            offer_refresh=True,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path == "/api/repos":
            self._handle_switch_repo()
            return
        if self.path == "/api/ticket-status":
            self._handle_toggle_ticket_status()
            return
        if self.path == "/api/refresh":
            self._handle_refresh()
            return
        self._send_json(404, {"error": "not found"})

    def _read_json_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def _handle_switch_repo(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            self._send_json(400, {"error": "invalid JSON"})
            return

        repo = str(payload.get("repo", "")).strip()
        if not repo_store.is_valid_repo(repo):
            self._send_json(400, {"error": "repo must look like owner/repo"})
            return

        state = repo_store.load_app_state()
        state = repo_store.switch_active_repo(state, repo)
        self._send_json(200, state)

    def _handle_toggle_ticket_status(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            self._send_json(400, {"error": "invalid JSON"})
            return

        try:
            ticket = int(payload.get("ticket"))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "ticket must be an integer"})
            return

        state = repo_store.load_app_state()
        try:
            new_status = repo_store.toggle_ticket_status(state["active_repo"], ticket)
        except ValueError as exc:
            self._send_json(404, {"error": str(exc)})
            return
        self._send_json(200, {"ticket": ticket, "status": new_status})

    def _handle_refresh(self) -> None:
        state = repo_store.load_app_state()
        repo = state["active_repo"]
        try:
            tickets, new_numbers = refresh_repo(repo)
        except Exception as exc:  # subprocess failure, malformed gh output, etc.
            self._send_json(502, {"error": f"GitHub refresh selhal: {exc}"})
            return
        _new_tickets_by_repo[repo] = new_numbers
        self._send_json(200, {"ticket_count": len(tickets), "new_tickets": sorted(new_numbers)})

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    repo_store.load_app_state()
    server = HTTPServer((HOST, PORT), DiagramHandler)
    print(f"Servíruji na http://{HOST}:{PORT} (zavri toto okno pro zastaveni)")
    server.serve_forever()


if __name__ == "__main__":
    main()
