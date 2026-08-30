"""Servíruje diagram ze stavu ticketu v tickets.txt na http://localhost:8765.

Pouziti:
    python server.py

Zadne externi zavislosti (jen standardni knihovna), zadne sitove pozadavky —
diagram je pri kazdem pozadavku prepocitan z tickets.txt stejne jako v
generate_diagram.py.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer

from generate_diagram import TICKETS_FILE, build_html, parse_tickets

HOST = "localhost"
PORT = 8765


class DiagramHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        tickets = parse_tickets(TICKETS_FILE)
        body = build_html(tickets).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        pass


def main() -> None:
    server = HTTPServer((HOST, PORT), DiagramHandler)
    print(f"Servíruji na http://{HOST}:{PORT} (zavri toto okno pro zastaveni)")
    server.serve_forever()


if __name__ == "__main__":
    main()
