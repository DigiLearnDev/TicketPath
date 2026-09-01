"""Unit tests for server.refresh_repo: the fetch -> diff -> save sequence that
used to live inside github_refresh.refresh_repo. Fetching is faked here, so
these tests never call `gh`."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import repo_store
import server


RAW_ISSUE_1 = {"number": 1, "title": "First", "state": "OPEN", "body": "", "subIssues": {"nodes": []}}
RAW_ISSUE_2 = {"number": 2, "title": "Second", "state": "CLOSED", "body": "", "subIssues": {"nodes": []}}


class RefreshRepoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(repo_store, "DATA_DIR", Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = "owner/repo"

    def test_fetches_translates_and_saves_without_calling_gh(self):
        with patch("github_refresh.fetch_all_issues", return_value=[RAW_ISSUE_1]) as fake_fetch:
            tickets, new_numbers = server.refresh_repo(self.repo)

        fake_fetch.assert_called_once_with(self.repo)
        self.assertEqual([t.number for t in tickets], [1])
        self.assertEqual(new_numbers, {1})

        saved_text = repo_store.tickets_path(self.repo).read_text(encoding="utf-8")
        self.assertIn("ticket: 1", saved_text)

    def test_second_refresh_only_flags_newly_discovered_tickets(self):
        with patch("github_refresh.fetch_all_issues", return_value=[RAW_ISSUE_1]):
            server.refresh_repo(self.repo)

        with patch("github_refresh.fetch_all_issues", return_value=[RAW_ISSUE_1, RAW_ISSUE_2]):
            tickets, new_numbers = server.refresh_repo(self.repo)

        self.assertEqual([t.number for t in tickets], [1, 2])
        self.assertEqual(new_numbers, {2})

    def test_preserves_existing_header_comment(self):
        repo_store.ensure_repo_files(self.repo)
        path = repo_store.tickets_path(self.repo)
        path.write_text("# my comment\n\n", encoding="utf-8")

        with patch("github_refresh.fetch_all_issues", return_value=[RAW_ISSUE_1]):
            server.refresh_repo(self.repo)

        saved_text = path.read_text(encoding="utf-8")
        self.assertTrue(saved_text.startswith("# my comment"))


if __name__ == "__main__":
    unittest.main()
