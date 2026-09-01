"""GitHub adapter: pulls all issues for a repo via `gh api graphql` (read-only,
relies on the machine's already-authenticated `gh` — no token is stored or
written to any file) and translates them into Tickets.

This module only downloads and translates — it never touches disk. The
refresh sequence (fetch -> diff against prior state -> save) is owned by the
caller (see server.py's refresh_repo), which is what keeps the dependency
arrows pointing one way and lets refresh be tested with a faked fetch, no
`gh` involved.
"""
from __future__ import annotations

import json
import re
import subprocess

from ticket import Ticket

GRAPHQL_QUERY = """
query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) {
    issues(first: 50, after: $after, states: [OPEN, CLOSED]) {
      pageInfo { hasNextPage endCursor }
      nodes {
        number
        title
        state
        body
        subIssues(first: 50) {
          nodes { number state }
        }
        labels(first: 10) {
          nodes { name }
        }
      }
    }
  }
}
"""

BLOCKED_BY_SECTION_RE = re.compile(
    r"^#{0,6}\s*blocked\s+by\s*:?\s*\n*(.*?)(?:\n\s*\n|\n##|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
ISSUE_REF_RE = re.compile(r"#(\d+)")
CHUNK_LABEL_RE = re.compile(r"\bchunk\s*#(\d+)", re.IGNORECASE)


def parse_blocked_by(body: str | None) -> list[int]:
    if not body:
        return []
    match = BLOCKED_BY_SECTION_RE.search(body)
    if not match:
        return []
    section = match.group(1).strip()
    if not section or section.lower().strip("_. ") == "none":
        return []
    return [int(n) for n in ISSUE_REF_RE.findall(section)]


def parse_chunk(labels: list[str]) -> int | None:
    """Extracts the chunk number from `Chunk #N` labels. With more than one
    matching label, the lowest number wins."""
    numbers = [int(m.group(1)) for label in labels if (m := CHUNK_LABEL_RE.search(label))]
    return min(numbers) if numbers else None


def sub_progress(sub_issues: list[dict]) -> tuple[int, int] | None:
    if not sub_issues:
        return None
    done = sum(1 for sub in sub_issues if sub["state"] == "CLOSED")
    return (done, len(sub_issues))


def build_tickets_from_issues(raw_issues: list[dict]) -> list[Ticket]:
    part_of: dict[int, int] = {}
    for issue in raw_issues:
        for sub in issue.get("subIssues", {}).get("nodes", []):
            part_of[sub["number"]] = issue["number"]

    tickets = []
    for issue in raw_issues:
        number = issue["number"]
        label_names = [n["name"] for n in issue.get("labels", {}).get("nodes", [])]
        tickets.append(
            Ticket(
                number=number,
                title=issue["title"],
                status="closed" if issue["state"] == "CLOSED" else "open",
                blocked_by=parse_blocked_by(issue.get("body")),
                part_of=part_of.get(number),
                chunk=parse_chunk(label_names),
                sub_progress=sub_progress(issue.get("subIssues", {}).get("nodes", [])),
            )
        )
    tickets.sort(key=lambda t: t.number)
    return tickets


def diff_new_tickets(old_numbers: set[int], new_numbers: set[int]) -> set[int]:
    return new_numbers - old_numbers


def fetch_all_issues(repo: str) -> list[dict]:
    owner, _, name = repo.partition("/")
    issues: list[dict] = []
    after: str | None = None

    while True:
        args = [
            "gh", "api", "graphql",
            "-f", f"query={GRAPHQL_QUERY}",
            "-F", f"owner={owner}",
            "-F", f"name={name}",
        ]
        if after:
            args += ["-F", f"after={after}"]
        # encoding="utf-8" explicitly: gh emits UTF-8, but text=True would decode
        # with the Windows locale (cp1250 here), which kills the reader thread on
        # accented issue titles and leaves result.stdout as None.
        result = subprocess.run(
            args, capture_output=True, check=True, encoding="utf-8", errors="replace"
        )
        payload = json.loads(result.stdout)
        page = payload["data"]["repository"]["issues"]
        issues.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        after = page["pageInfo"]["endCursor"]

    return issues
