"""Unit tests for repo_store.toggle_ticket_status."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import repo_store


class RepoShortNameTests(unittest.TestCase):
    def test_default_repo_matches_legacy_wording(self):
        self.assertEqual(repo_store.repo_short_name("DigiLearnDev/DigiLearn"), "DigiLearn")

    def test_other_repo(self):
        self.assertEqual(repo_store.repo_short_name("DigiLearnDev/TicketPath"), "TicketPath")


SAMPLE = """# comment line, should be preserved
# another comment

ticket: 19
title: Block Registry foundation: Text kind
status: closed
blocked_by: none

ticket: 20
title: Second ticket
status: open
blocked_by: 19
"""


class ToggleTicketStatusDelegationTests(unittest.TestCase):
    """repo_store no longer knows the tickets.txt format itself — it just
    resolves the repo's file path and delegates to ticket_store. Format
    details (header preservation, unknown-ticket errors, etc.) are covered
    in test_ticket_store.py."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._orig_data_dir = repo_store.DATA_DIR
        repo_store.DATA_DIR = Path(self.tmp.name) / "data"
        self.addCleanup(self._restore_data_dir)
        self.repo = "owner/repo"
        repo_store.ensure_repo_files(self.repo)
        repo_store.tickets_path(self.repo).write_text(SAMPLE, encoding="utf-8")

    def _restore_data_dir(self):
        repo_store.DATA_DIR = self._orig_data_dir

    def test_toggle_closed_to_open(self):
        new_status = repo_store.toggle_ticket_status(self.repo, 19)
        self.assertEqual(new_status, "open")
        text = repo_store.tickets_path(self.repo).read_text(encoding="utf-8")
        self.assertIn("status: open", text)

    def test_unknown_ticket_raises(self):
        with self.assertRaises(ValueError):
            repo_store.toggle_ticket_status(self.repo, 999)


if __name__ == "__main__":
    unittest.main()
