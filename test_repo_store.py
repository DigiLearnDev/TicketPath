"""Unit tests for repo_store.toggle_ticket_status."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import repo_store


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


class ToggleTicketStatusTests(unittest.TestCase):
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
        self.assertIn("ticket: 19\ntitle: Block Registry foundation: Text kind\nstatus: open\n", text)

    def test_toggle_open_to_closed(self):
        new_status = repo_store.toggle_ticket_status(self.repo, 20)
        self.assertEqual(new_status, "closed")
        text = repo_store.tickets_path(self.repo).read_text(encoding="utf-8")
        self.assertIn("ticket: 20\ntitle: Second ticket\nstatus: closed\n", text)

    def test_other_ticket_and_comments_untouched(self):
        repo_store.toggle_ticket_status(self.repo, 19)
        text = repo_store.tickets_path(self.repo).read_text(encoding="utf-8")
        self.assertIn("# comment line, should be preserved", text)
        self.assertIn("ticket: 20\ntitle: Second ticket\nstatus: open\n", text)

    def test_unknown_ticket_raises(self):
        with self.assertRaises(ValueError):
            repo_store.toggle_ticket_status(self.repo, 999)

    def test_toggle_persists_across_reload(self):
        repo_store.toggle_ticket_status(self.repo, 19)
        text_first = repo_store.tickets_path(self.repo).read_text(encoding="utf-8")
        text_second = repo_store.tickets_path(self.repo).read_text(encoding="utf-8")
        self.assertEqual(text_first, text_second)
        self.assertIn("status: open", text_second)


if __name__ == "__main__":
    unittest.main()
