"""Unit tests for ticket_store: the sole owner of the tickets.txt format —
parsing, rendering, header preservation, and status toggling."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import ticket_store
from ticket import Ticket


class SplitHeaderTests(unittest.TestCase):
    def test_splits_before_first_ticket_line(self):
        text = "# comment\n# more\n\nticket: 1\ntitle: T\nstatus: open\nblocked_by: none\n"
        header = ticket_store.split_header(text)
        self.assertEqual(header, "# comment\n# more\n\n")

    def test_empty_text_returns_empty_header(self):
        self.assertEqual(ticket_store.split_header(""), "")

    def test_no_ticket_lines_returns_whole_text_as_header(self):
        text = "# just comments\n# nothing else\n"
        self.assertEqual(ticket_store.split_header(text), text)


class ParseTicketsTests(unittest.TestCase):
    def test_parses_basic_fields(self):
        text = (
            "ticket: 1\ntitle: First\nstatus: open\nblocked_by: none\n\n"
            "ticket: 2\ntitle: Second\nstatus: closed\nblocked_by: 1\n"
        )
        tickets = ticket_store.parse_tickets(text)
        self.assertEqual([t.number for t in tickets], [1, 2])
        self.assertEqual(tickets[1].blocked_by, [1])
        self.assertEqual(tickets[1].status, "closed")

    def test_ignores_comment_lines(self):
        text = "# a comment\nticket: 1\ntitle: T\nstatus: open\nblocked_by: none\n"
        tickets = ticket_store.parse_tickets(text)
        self.assertEqual(len(tickets), 1)

    def test_optional_fields_default_to_none(self):
        text = "ticket: 1\ntitle: T\nstatus: open\nblocked_by: none\n"
        t = ticket_store.parse_tickets(text)[0]
        self.assertIsNone(t.part_of)
        self.assertIsNone(t.chunk)
        self.assertIsNone(t.sub_progress)

    def test_sorted_by_number(self):
        text = "ticket: 5\ntitle: B\nstatus: open\nblocked_by: none\n\nticket: 1\ntitle: A\nstatus: open\nblocked_by: none\n"
        tickets = ticket_store.parse_tickets(text)
        self.assertEqual([t.number for t in tickets], [1, 5])


class RenderTicketsFileTests(unittest.TestCase):
    def test_preserves_header_and_renders_blocks(self):
        header = "# a header comment\n# more comment\n\n"
        tickets = [
            Ticket(number=1, title="First", status="open", blocked_by=[], part_of=None),
            Ticket(number=2, title="Second", status="closed", blocked_by=[1], part_of=None),
        ]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertTrue(text.startswith(header))
        self.assertIn("ticket: 1\ntitle: First\nstatus: open\nblocked_by: none\n", text)
        self.assertIn("ticket: 2\ntitle: Second\nstatus: closed\nblocked_by: 1\n", text)

    def test_part_of_line_emitted_when_present(self):
        header = ""
        tickets = [Ticket(number=36, title="Child", status="open", blocked_by=[], part_of=29)]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertIn("part_of: 29\n", text)

    def test_part_of_line_omitted_when_absent(self):
        header = ""
        tickets = [Ticket(number=1, title="T", status="open", blocked_by=[], part_of=None)]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertNotIn("part_of:", text)

    def test_chunk_line_emitted_when_present(self):
        header = ""
        tickets = [Ticket(number=1, title="T", status="open", blocked_by=[], part_of=None, chunk=2)]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertIn("chunk: 2\n", text)

    def test_chunk_line_omitted_when_absent(self):
        header = ""
        tickets = [Ticket(number=1, title="T", status="open", blocked_by=[], part_of=None, chunk=None)]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertNotIn("chunk:", text)

    def test_sub_progress_line_emitted_when_present(self):
        header = ""
        tickets = [
            Ticket(number=29, title="Parent", status="open", blocked_by=[], part_of=None, sub_progress=(1, 2))
        ]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertIn("sub_progress: 1/2\n", text)

    def test_sub_progress_line_omitted_when_absent(self):
        header = ""
        tickets = [
            Ticket(number=1, title="T", status="open", blocked_by=[], part_of=None, sub_progress=None)
        ]
        text = ticket_store.render_tickets_file(header, tickets)
        self.assertNotIn("sub_progress:", text)


class RoundTripTests(unittest.TestCase):
    def test_saved_file_loads_back_to_same_tickets(self):
        tickets = [
            Ticket(number=1, title="First", status="open", blocked_by=[], part_of=None, chunk=None),
            Ticket(
                number=2,
                title="Second",
                status="closed",
                blocked_by=[1],
                part_of=1,
                chunk=2,
                sub_progress=(3, 5),
            ),
        ]
        with TemporaryDirectory() as d:
            path = Path(d) / "tickets.txt"
            ticket_store.save_tickets(path, tickets, header="# header\n\n")
            loaded = ticket_store.load_tickets(path)

        self.assertEqual(loaded, tickets)

    def test_header_preserved_on_save(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "tickets.txt"
            header = "# comment line, should be preserved\n\n"
            ticket_store.save_tickets(path, [], header=header)
            text = path.read_text(encoding="utf-8")

        self.assertTrue(text.startswith(header))


class ToggleStatusTests(unittest.TestCase):
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

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "tickets.txt"
        self.path.write_text(self.SAMPLE, encoding="utf-8")

    def test_toggle_closed_to_open(self):
        new_status = ticket_store.toggle_status(self.path, 19)
        self.assertEqual(new_status, "open")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("ticket: 19\ntitle: Block Registry foundation: Text kind\nstatus: open\n", text)

    def test_toggle_open_to_closed(self):
        new_status = ticket_store.toggle_status(self.path, 20)
        self.assertEqual(new_status, "closed")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("ticket: 20\ntitle: Second ticket\nstatus: closed\n", text)

    def test_other_ticket_and_header_untouched(self):
        ticket_store.toggle_status(self.path, 19)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# comment line, should be preserved", text)
        self.assertIn("ticket: 20\ntitle: Second ticket\nstatus: open\n", text)

    def test_unknown_ticket_raises(self):
        with self.assertRaises(ValueError):
            ticket_store.toggle_status(self.path, 999)

    def test_toggle_persists_across_reload(self):
        ticket_store.toggle_status(self.path, 19)
        text_first = self.path.read_text(encoding="utf-8")
        text_second = self.path.read_text(encoding="utf-8")
        self.assertEqual(text_first, text_second)
        self.assertIn("status: open", text_second)


if __name__ == "__main__":
    unittest.main()
