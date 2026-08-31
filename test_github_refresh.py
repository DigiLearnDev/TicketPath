"""Unit tests for github_refresh: body parsing, issue->ticket mapping, file rendering."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import github_refresh as gr


class ParseBlockedByTests(unittest.TestCase):
    def test_no_section_returns_empty(self):
        self.assertEqual(gr.parse_blocked_by("Just a description, no blockers."), [])

    def test_heading_style_single_line(self):
        body = "## Blocked by\n\n#2, #8"
        self.assertEqual(gr.parse_blocked_by(body), [2, 8])

    def test_inline_style(self):
        body = "Some text.\n\nBlocked by: #5\n\nMore text."
        self.assertEqual(gr.parse_blocked_by(body), [5])

    def test_stops_at_next_heading(self):
        body = "## Blocked by\n\n#2, #8\n\n## Other section\n\n#99"
        self.assertEqual(gr.parse_blocked_by(body), [2, 8])

    def test_none_variants_return_empty(self):
        for body in ["## Blocked by\n\nnone", "## Blocked by\n\n_none_", "## Blocked by\n\nNone."]:
            self.assertEqual(gr.parse_blocked_by(body), [], body)

    def test_none_body_returns_empty(self):
        self.assertEqual(gr.parse_blocked_by(None), [])

    def test_incidental_phrase_before_heading_does_not_shadow_real_section(self):
        body = (
            "This design was previously blocked by budget approval, now approved.\n\n"
            "## Blocked by\n\n#5"
        )
        self.assertEqual(gr.parse_blocked_by(body), [5])


class BuildTicketsFromIssuesTests(unittest.TestCase):
    def test_basic_mapping(self):
        raw = [
            {"number": 1, "title": "First", "state": "OPEN", "body": "", "subIssues": {"nodes": []}},
            {
                "number": 2,
                "title": "Second",
                "state": "CLOSED",
                "body": "## Blocked by\n\n#1",
                "subIssues": {"nodes": []},
            },
        ]
        tickets = gr.build_tickets_from_issues(raw)
        self.assertEqual(tickets[0]["number"], 1)
        self.assertEqual(tickets[0]["status"], "open")
        self.assertEqual(tickets[0]["blocked_by"], [])
        self.assertIsNone(tickets[0]["part_of"])
        self.assertEqual(tickets[1]["status"], "closed")
        self.assertEqual(tickets[1]["blocked_by"], [1])

    def test_sub_issues_become_part_of_badge_only(self):
        raw = [
            {
                "number": 29,
                "title": "Parent",
                "state": "OPEN",
                "body": "",
                "subIssues": {"nodes": [{"number": 36, "state": "OPEN"}, {"number": 37, "state": "CLOSED"}]},
            },
            {"number": 36, "title": "Child A", "state": "OPEN", "body": "", "subIssues": {"nodes": []}},
            {"number": 37, "title": "Child B", "state": "CLOSED", "body": "", "subIssues": {"nodes": []}},
        ]
        tickets = gr.build_tickets_from_issues(raw)
        by_number = {t["number"]: t for t in tickets}
        self.assertEqual(by_number[36]["part_of"], 29)
        self.assertEqual(by_number[37]["part_of"], 29)
        self.assertEqual(by_number[36]["blocked_by"], [])
        self.assertIsNone(by_number[29]["part_of"])

    def test_sorted_by_number(self):
        raw = [
            {"number": 5, "title": "B", "state": "OPEN", "body": "", "subIssues": {"nodes": []}},
            {"number": 1, "title": "A", "state": "OPEN", "body": "", "subIssues": {"nodes": []}},
        ]
        tickets = gr.build_tickets_from_issues(raw)
        self.assertEqual([t["number"] for t in tickets], [1, 5])


class DiffNewTicketsTests(unittest.TestCase):
    def test_new_numbers_only(self):
        old = {1, 2, 3}
        new = {1, 2, 3, 4, 5}
        self.assertEqual(gr.diff_new_tickets(old, new), {4, 5})

    def test_no_new_numbers(self):
        self.assertEqual(gr.diff_new_tickets({1, 2}, {1, 2}), set())

    def test_empty_old_marks_all_new(self):
        self.assertEqual(gr.diff_new_tickets(set(), {1, 2}), {1, 2})


class RenderTicketsFileTests(unittest.TestCase):
    def test_preserves_header_and_renders_blocks(self):
        header = "# a header comment\n# more comment\n\n"
        tickets = [
            {"number": 1, "title": "First", "status": "open", "blocked_by": [], "part_of": None},
            {"number": 2, "title": "Second", "status": "closed", "blocked_by": [1], "part_of": None},
        ]
        text = gr.render_tickets_file(header, tickets)
        self.assertTrue(text.startswith(header))
        self.assertIn("ticket: 1\ntitle: First\nstatus: open\nblocked_by: none\n", text)
        self.assertIn("ticket: 2\ntitle: Second\nstatus: closed\nblocked_by: 1\n", text)

    def test_part_of_line_emitted_when_present(self):
        header = ""
        tickets = [{"number": 36, "title": "Child", "status": "open", "blocked_by": [], "part_of": 29}]
        text = gr.render_tickets_file(header, tickets)
        self.assertIn("part_of: 29\n", text)

    def test_part_of_line_omitted_when_absent(self):
        header = ""
        tickets = [{"number": 1, "title": "T", "status": "open", "blocked_by": [], "part_of": None}]
        text = gr.render_tickets_file(header, tickets)
        self.assertNotIn("part_of:", text)

    def test_round_trip_with_parse_tickets(self):
        import tempfile
        from pathlib import Path

        import generate_diagram

        header = "# header\n\n"
        tickets = [
            {"number": 1, "title": "First", "status": "open", "blocked_by": [], "part_of": None},
            {"number": 2, "title": "Second", "status": "closed", "blocked_by": [1], "part_of": 1},
        ]
        text = gr.render_tickets_file(header, tickets)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "tickets.txt"
            p.write_text(text, encoding="utf-8")
            parsed = generate_diagram.parse_tickets(p)
        self.assertEqual(parsed[0]["blocked_by"], [])
        self.assertEqual(parsed[1]["blocked_by"], [1])
        self.assertEqual(parsed[1]["part_of"], 1)


class SplitHeaderTests(unittest.TestCase):
    def test_splits_before_first_ticket_line(self):
        text = "# comment\n# more\n\nticket: 1\ntitle: T\nstatus: open\nblocked_by: none\n"
        header = gr.split_header(text)
        self.assertEqual(header, "# comment\n# more\n\n")

    def test_empty_text_returns_empty_header(self):
        self.assertEqual(gr.split_header(""), "")

    def test_no_ticket_lines_returns_whole_text_as_header(self):
        text = "# just comments\n# nothing else\n"
        self.assertEqual(gr.split_header(text), text)


class FetchAllIssuesTests(unittest.TestCase):
    @patch("github_refresh.subprocess.run")
    def test_single_page(self, mock_run):
        mock_run.return_value.stdout = (
            '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false,"endCursor":null},'
            '"nodes":[{"number":1,"title":"T","state":"OPEN","body":"","subIssues":{"nodes":[]}}]}}}}'
        )
        issues = gr.fetch_all_issues("owner/repo")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["number"], 1)
        mock_run.assert_called_once()

    @patch("github_refresh.subprocess.run")
    def test_paginates_until_no_next_page(self, mock_run):
        page1 = (
            '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":true,"endCursor":"CUR"},'
            '"nodes":[{"number":1,"title":"A","state":"OPEN","body":"","subIssues":{"nodes":[]}}]}}}}'
        )
        page2 = (
            '{"data":{"repository":{"issues":{"pageInfo":{"hasNextPage":false,"endCursor":null},'
            '"nodes":[{"number":2,"title":"B","state":"OPEN","body":"","subIssues":{"nodes":[]}}]}}}}'
        )
        mock_run.side_effect = [
            type("R", (), {"stdout": page1})(),
            type("R", (), {"stdout": page2})(),
        ]
        issues = gr.fetch_all_issues("owner/repo")
        self.assertEqual([i["number"] for i in issues], [1, 2])
        self.assertEqual(mock_run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
