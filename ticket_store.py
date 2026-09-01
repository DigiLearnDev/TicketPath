"""Owns the tickets.txt file format.

The only module that knows the on-disk syntax of tickets.txt: parsing text
into `Ticket` records, rendering `Ticket` records back into text (preserving
any header comment before the first block), and flipping a single ticket's
status via load -> change field -> save. Everything else (diagram rendering,
the GitHub refresh, the HTTP handlers) works with `Ticket` objects and calls
into this module for file I/O — none of it parses or writes the format
itself.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from ticket import Ticket

_TICKET_LINE_RE = re.compile(r"^ticket:\s*\d+\s*$", re.MULTILINE)


def split_header(text: str) -> str:
    """Everything before the first 'ticket:' block line — preserved verbatim on save."""
    match = _TICKET_LINE_RE.search(text)
    if not match:
        return text
    return text[: match.start()]


def parse_tickets(text: str) -> list[Ticket]:
    lines = text.splitlines()

    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if stripped == "":
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(stripped)
    if current:
        blocks.append(current)

    tickets = []
    for block in blocks:
        data: dict[str, str] = {}
        for line in block:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
        if "ticket" not in data:
            continue

        number = int(data["ticket"])
        title = data.get("title", f"#{number}")
        status = data.get("status", "open").strip().lower()
        blocked_raw = data.get("blocked_by", "none").strip().lower()
        if blocked_raw in ("none", ""):
            blocked_by: list[int] = []
        else:
            blocked_by = [int(x.strip()) for x in blocked_raw.split(",") if x.strip()]

        part_of_raw = data.get("part_of", "").strip()
        part_of = int(part_of_raw) if part_of_raw else None

        chunk_raw = data.get("chunk", "").strip()
        chunk = int(chunk_raw) if chunk_raw else None

        sub_progress_raw = data.get("sub_progress", "").strip()
        if sub_progress_raw and "/" in sub_progress_raw:
            done_str, _, total_str = sub_progress_raw.partition("/")
            sub_progress = (int(done_str.strip()), int(total_str.strip()))
        else:
            sub_progress = None

        labels_raw = data.get("labels", "").strip()
        labels = [x.strip() for x in labels_raw.split(",") if x.strip()] if labels_raw else []

        tickets.append(
            Ticket(
                number=number,
                title=title,
                status=status,
                blocked_by=blocked_by,
                part_of=part_of,
                chunk=chunk,
                sub_progress=sub_progress,
                labels=labels,
            )
        )

    tickets.sort(key=lambda t: t.number)
    return tickets


def render_tickets_file(header: str, tickets: list[Ticket]) -> str:
    blocks = []
    for t in tickets:
        blocked_by = ",".join(str(b) for b in t.blocked_by) if t.blocked_by else "none"
        lines = [
            f"ticket: {t.number}",
            f"title: {t.title}",
            f"status: {t.status}",
            f"blocked_by: {blocked_by}",
        ]
        if t.part_of is not None:
            lines.append(f"part_of: {t.part_of}")
        if t.chunk is not None:
            lines.append(f"chunk: {t.chunk}")
        if t.sub_progress is not None:
            done, total = t.sub_progress
            lines.append(f"sub_progress: {done}/{total}")
        if t.labels:
            lines.append(f"labels: {','.join(t.labels)}")
        blocks.append("\n".join(lines) + "\n")
    return header + "\n".join(blocks)


def load_tickets(path: Path) -> list[Ticket]:
    return parse_tickets(path.read_text(encoding="utf-8"))


def save_tickets(path: Path, tickets: list[Ticket], header: str = "") -> None:
    path.write_text(render_tickets_file(header, tickets), encoding="utf-8")


def toggle_status(path: Path, ticket_number: int) -> str:
    """Flips one ticket's status: load the file, change that ticket's field,
    save it back. The new status is a stand-in value until the next GitHub
    refresh overwrites it with the real state of the issue — not a separate
    override layer.
    """
    text = path.read_text(encoding="utf-8")
    header = split_header(text)
    tickets = parse_tickets(text)

    new_status = ""
    updated: list[Ticket] = []
    for t in tickets:
        if t.number == ticket_number:
            new_status = "open" if t.status == "closed" else "closed"
            t = dataclasses.replace(t, status=new_status)
        updated.append(t)

    if not new_status:
        raise ValueError(f"ticket {ticket_number} not found in {path}")

    save_tickets(path, updated, header)
    return new_status
