"""GitHub refresh: pulls all issues for a repo via `gh api graphql` (read-only,
relies on the machine's already-authenticated `gh` — no token is stored or
written to any file) and turns them into tickets.txt records.

Kept separate from repo_store.py (local persistence) and generate_diagram.py
(rendering) so the GitHub-specific parsing/query logic has its own seam.
"""
from __future__ import annotations

import json
import re
import subprocess

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


def build_tickets_from_issues(raw_issues: list[dict]) -> list[dict]:
    part_of: dict[int, int] = {}
    for issue in raw_issues:
        for sub in issue.get("subIssues", {}).get("nodes", []):
            part_of[sub["number"]] = issue["number"]

    tickets = []
    for issue in raw_issues:
        number = issue["number"]
        tickets.append(
            {
                "number": number,
                "title": issue["title"],
                "status": "closed" if issue["state"] == "CLOSED" else "open",
                "blocked_by": parse_blocked_by(issue.get("body")),
                "part_of": part_of.get(number),
            }
        )
    tickets.sort(key=lambda t: t["number"])
    return tickets


def diff_new_tickets(old_numbers: set[int], new_numbers: set[int]) -> set[int]:
    return new_numbers - old_numbers


def split_header(text: str) -> str:
    """Everything before the first 'ticket:' block line — preserved verbatim on refresh."""
    match = re.search(r"^ticket:\s*\d+\s*$", text, re.MULTILINE)
    if not match:
        return text
    return text[: match.start()]


def render_tickets_file(header: str, tickets: list[dict]) -> str:
    blocks = []
    for t in tickets:
        blocked_by = ",".join(str(b) for b in t["blocked_by"]) if t["blocked_by"] else "none"
        lines = [
            f"ticket: {t['number']}",
            f"title: {t['title']}",
            f"status: {t['status']}",
            f"blocked_by: {blocked_by}",
        ]
        if t.get("part_of") is not None:
            lines.append(f"part_of: {t['part_of']}")
        blocks.append("\n".join(lines) + "\n")
    return header + "\n".join(blocks)


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


def refresh_repo(repo: str) -> tuple[list[dict], set[int]]:
    """Fetches fresh issue data for `repo`, rewrites its tickets.txt, and returns
    (tickets, newly_discovered_ticket_numbers). Never touches diagram-state.json —
    the "new" badge is transient (in-memory, per the spec) and disappears on the
    next refresh with no persisted dismiss state.
    """
    import generate_diagram
    import repo_store

    path = repo_store.tickets_path(repo)
    old_text = path.read_text(encoding="utf-8") if path.exists() else ""
    old_tickets = generate_diagram.parse_tickets(path) if path.exists() else []
    old_numbers = {t["number"] for t in old_tickets}

    raw_issues = fetch_all_issues(repo)
    tickets = build_tickets_from_issues(raw_issues)
    new_numbers = diff_new_tickets(old_numbers, {t["number"] for t in tickets})

    header = split_header(old_text)
    path.write_text(render_tickets_file(header, tickets), encoding="utf-8")

    return tickets, new_numbers
