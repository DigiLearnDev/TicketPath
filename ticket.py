"""The single definition of what a ticket is.

Two constructors build this record — the tickets.txt parser
(generate_diagram.parse_tickets) and the GitHub adapter
(github_refresh.build_tickets_from_issues) — and everything else (layout,
card state, rendering) only reads it, via attributes rather than dict keys.
That makes optional fields explicit and turns a typo'd field name into an
AttributeError instead of a silent None.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ticket:
    number: int
    title: str
    status: str
    blocked_by: list[int]
    part_of: int | None = None
    chunk: int | None = None
    sub_progress: tuple[int, int] | None = None
