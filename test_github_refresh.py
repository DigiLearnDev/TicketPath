"""Unit tests for github_refresh: body parsing, issue->ticket mapping, file rendering."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import github_refresh as gr
from ticket import Ticket


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


class ParseChunkTests(unittest.TestCase):
    def test_no_matching_label_returns_none(self):
        self.assertIsNone(gr.parse_chunk(["bug", "priority: high"]))

    def test_empty_labels_returns_none(self):
        self.assertIsNone(gr.parse_chunk([]))

    def test_single_chunk_label(self):
        self.assertEqual(gr.parse_chunk(["Chunk #2", "bug"]), 2)

    def test_case_insensitive(self):
        self.assertEqual(gr.parse_chunk(["chunk #3"]), 3)

    def test_multiple_chunk_labels_uses_lowest(self):
        self.assertEqual(gr.parse_chunk(["Chunk #3", "Chunk #1"]), 1)

    def test_substring_match_does_not_count_as_chunk_label(self):
        self.assertIsNone(gr.parse_chunk(["rechunk #4"]))
        self.assertIsNone(gr.parse_chunk(["prechunking #4"]))


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
        self.assertEqual(tickets[0].number, 1)
        self.assertEqual(tickets[0].status, "open")
        self.assertEqual(tickets[0].blocked_by, [])
        self.assertIsNone(tickets[0].part_of)
        self.assertIsNone(tickets[0].chunk)
        self.assertEqual(tickets[1].status, "closed")
        self.assertEqual(tickets[1].blocked_by, [1])

    def test_chunk_label_extracted(self):
        raw = [
            {
                "number": 1,
                "title": "First",
                "state": "OPEN",
                "body": "",
                "subIssues": {"nodes": []},
                "labels": {"nodes": [{"name": "Chunk #1"}, {"name": "bug"}]},
            },
        ]
        tickets = gr.build_tickets_from_issues(raw)
        self.assertEqual(tickets[0].chunk, 1)

    def test_missing_labels_field_defaults_to_no_chunk(self):
        raw = [{"number": 1, "title": "First", "state": "OPEN", "body": "", "subIssues": {"nodes": []}}]
        tickets = gr.build_tickets_from_issues(raw)
        self.assertIsNone(tickets[0].chunk)

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
        by_number = {t.number: t for t in tickets}
        self.assertEqual(by_number[36].part_of, 29)
        self.assertEqual(by_number[37].part_of, 29)
        self.assertEqual(by_number[36].blocked_by, [])
        self.assertIsNone(by_number[29].part_of)

    def test_sub_progress_counts_done_over_total(self):
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
        by_number = {t.number: t for t in tickets}
        self.assertEqual(by_number[29].sub_progress, (1, 2))
        self.assertIsNone(by_number[36].sub_progress)

    def test_sub_progress_none_when_no_sub_issues(self):
        raw = [{"number": 1, "title": "First", "state": "OPEN", "body": "", "subIssues": {"nodes": []}}]
        tickets = gr.build_tickets_from_issues(raw)
        self.assertIsNone(tickets[0].sub_progress)

    def test_sorted_by_number(self):
        raw = [
            {"number": 5, "title": "B", "state": "OPEN", "body": "", "subIssues": {"nodes": []}},
            {"number": 1, "title": "A", "state": "OPEN", "body": "", "subIssues": {"nodes": []}},
        ]
        tickets = gr.build_tickets_from_issues(raw)
        self.assertEqual([t.number for t in tickets], [1, 5])


class DiffNewTicketsTests(unittest.TestCase):
    def test_new_numbers_only(self):
        old = {1, 2, 3}
        new = {1, 2, 3, 4, 5}
        self.assertEqual(gr.diff_new_tickets(old, new), {4, 5})

    def test_no_new_numbers(self):
        self.assertEqual(gr.diff_new_tickets({1, 2}, {1, 2}), set())

    def test_empty_old_marks_all_new(self):
        self.assertEqual(gr.diff_new_tickets(set(), {1, 2}), {1, 2})


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

class FetchAllIssuesDecodingTests(unittest.TestCase):
    """Regression: gh emits UTF-8, so fetch_all_issues must decode as UTF-8 regardless
    of the machine's locale. With text=True, a cp1250 Windows box killed subprocess's
    reader thread on an accented issue title and left result.stdout as None, which
    surfaced as "the JSON object must be str, bytes or bytearray, not NoneType".

    Mocking subprocess.run cannot catch this — the decode happens inside it — so this
    test runs a real child process that prints UTF-8 and forwards the real kwargs.
    """

    def test_utf8_issue_titles_survive_a_non_utf8_locale(self):
        payload = json.dumps(
            {
                "data": {
                    "repository": {
                        "issues": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "number": 1,
                                    "title": "Přidat příšerně dlouhý název — ěščřžýáíé",
                                    "state": "OPEN",
                                    "body": "",
                                    "subIssues": {"nodes": []},
                                }
                            ],
                        }
                    }
                }
            },
            ensure_ascii=False,
        )
        child = (
            "import sys;"
            "sys.stdout.buffer.write(sys.argv[1].encode('utf-8'))"
        )
        real_run = subprocess.run

        def fake_gh(args, **kwargs):
            return real_run([sys.executable, "-c", child, payload], **kwargs)

        with patch("github_refresh.subprocess.run", fake_gh),                 patch.dict(os.environ, {"PYTHONIOENCODING": "cp1250"}):
            issues = gr.fetch_all_issues("owner/repo")

        self.assertEqual(issues[0]["title"], "Přidat příšerně dlouhý název — ěščřžýáíé")


if __name__ == "__main__":
    unittest.main()
