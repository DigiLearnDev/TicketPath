"""Unit tests for generate_diagram.effective_layers_and_dividers (chunk-based
auto phase layout) — pure functions, no I/O."""
from __future__ import annotations

import unittest

import generate_diagram as gd
from ticket import Ticket


def ticket(number, blocked_by=None, chunk=None, sub_progress=None, labels=None):
    return Ticket(
        number=number,
        title=f"T{number}",
        status="open",
        blocked_by=blocked_by or [],
        part_of=None,
        chunk=chunk,
        sub_progress=sub_progress,
        labels=labels or [],
    )


class EffectiveLayersAndDividersTests(unittest.TestCase):
    def test_ticket_without_chunk_unaffected(self):
        tickets = [ticket(1), ticket(2, blocked_by=[1])]
        computed = gd.compute_layers(tickets)
        effective, dividers = gd.effective_layers_and_dividers(tickets)
        self.assertEqual(effective, computed)
        self.assertEqual(dividers, [])

    def test_chunk_2_ticket_pushed_past_chunk_1_boundary(self):
        tickets = [ticket(1, chunk=1), ticket(2, chunk=2)]
        computed = gd.compute_layers(tickets)
        self.assertEqual(computed[1], 0)
        self.assertEqual(computed[2], 0)
        effective, dividers = gd.effective_layers_and_dividers(tickets)
        self.assertEqual(effective[1], 0)
        self.assertEqual(effective[2], 1)

    def test_own_blocked_by_can_push_past_chunk_boundary(self):
        # Ticket 3 is chunk 2, but is also blocked_by a chunk-1 ticket sitting
        # two layers deep — its own blocked_by chain wins over the chunk floor.
        tickets = [
            ticket(1, chunk=1),
            ticket(2, blocked_by=[1], chunk=1),
            ticket(3, blocked_by=[2], chunk=2),
        ]
        computed = gd.compute_layers(tickets)
        self.assertEqual(computed[3], 2)
        effective, dividers = gd.effective_layers_and_dividers(tickets)
        self.assertEqual(effective[3], 2)

    def test_gap_in_chunk_numbering_still_chains(self):
        # No ticket carries chunk 2 — chunk 3 tickets should still be pushed
        # past chunk 1's boundary.
        tickets = [ticket(1, chunk=1), ticket(2, chunk=3)]
        effective, dividers = gd.effective_layers_and_dividers(tickets)
        self.assertEqual(effective[2], 1)
        labels = [d["label"] for d in dividers]
        self.assertEqual(labels, ["Chunk #1", "Chunk #3"])

    def test_chunk_with_no_tickets_creates_no_divider(self):
        tickets = [ticket(1, chunk=1)]
        _, dividers = gd.effective_layers_and_dividers(tickets)
        self.assertEqual(len(dividers), 1)
        self.assertEqual(dividers[0]["label"], "Chunk #1")

    def test_divider_label_matches_chunk_text(self):
        tickets = [ticket(1, chunk=1), ticket(2, chunk=2)]
        _, dividers = gd.effective_layers_and_dividers(tickets)
        self.assertEqual(dividers[0]["label"], "Chunk #1")
        self.assertEqual(dividers[1]["label"], "Chunk #2")

    def test_divider_anchored_before_leftmost_ticket_in_its_chunk(self):
        tickets = [ticket(1, chunk=1), ticket(2, blocked_by=[1], chunk=1), ticket(3, chunk=2)]
        effective, dividers = gd.effective_layers_and_dividers(tickets)
        chunk1_divider = dividers[0]
        self.assertEqual(chunk1_divider["before_layer"], effective[1])
        self.assertEqual(effective[3], effective[2] + 1)

    def test_chunk_push_propagates_to_unchunked_dependent(self):
        # Ticket 2 (chunk 2, no blockers of its own) gets pushed past chunk 1's
        # boundary. Ticket 3 has no chunk but is blocked_by ticket 2 — it must
        # render strictly right of ticket 2's *pushed* layer, not its
        # pre-push blocked_by-only layer.
        tickets = [
            ticket(1, chunk=1),
            ticket(2, chunk=2),
            ticket(3, blocked_by=[2]),
        ]
        effective, _ = gd.effective_layers_and_dividers(tickets)
        self.assertGreater(effective[3], effective[2])

    def test_chunk_push_propagates_within_same_chunk_blocked_by_chain(self):
        # Both C and D carry chunk 2 and get floor-pushed, but D is also
        # blocked_by C within that same chunk — D must still render strictly
        # right of C, not land in the same column.
        tickets = [
            ticket(1, chunk=1),
            ticket(2, blocked_by=[1], chunk=1),
            ticket(3, chunk=2),
            ticket(4, blocked_by=[3], chunk=2),
        ]
        effective, _ = gd.effective_layers_and_dividers(tickets)
        self.assertGreater(effective[4], effective[3])


class ComputeDiagramLayoutTests(unittest.TestCase):
    def test_sub_progress_passes_through_unchanged(self):
        tickets = [ticket(1, sub_progress=(2, 5))]
        layout = gd.compute_diagram_layout(tickets)
        card = layout.columns[0].cards[0]
        self.assertEqual(card.ticket.sub_progress, (2, 5))

    def test_badge_is_ready_when_no_blockers(self):
        tickets = [ticket(1)]
        layout = gd.compute_diagram_layout(tickets)
        card = layout.columns[0].cards[0]
        self.assertEqual(card.state, "ready")
        self.assertEqual(card.badge, "ready")

    def test_badge_is_blocked_when_blocker_open(self):
        tickets = [ticket(1), ticket(2, blocked_by=[1])]
        layout = gd.compute_diagram_layout(tickets)
        card2 = next(c for col in layout.columns for c in col.cards if c.ticket.number == 2)
        self.assertEqual(card2.state, "blocked")
        self.assertEqual(card2.badge, "blocked")

    def test_badge_is_new_even_when_state_is_ready(self):
        tickets = [ticket(1)]
        layout = gd.compute_diagram_layout(tickets, new_tickets={1})
        card = layout.columns[0].cards[0]
        self.assertEqual(card.state, "ready")
        self.assertEqual(card.badge, "new")

    def test_badge_is_none_when_done(self):
        t = ticket(1)
        t = gd.Ticket(**{**t.__dict__, "status": "closed"})
        layout = gd.compute_diagram_layout([t])
        card = layout.columns[0].cards[0]
        self.assertEqual(card.state, "done")
        self.assertIsNone(card.badge)

    def test_deps_reflect_blocker_title_and_done_status(self):
        blocker = ticket(1)
        blocker = gd.Ticket(**{**blocker.__dict__, "title": "Blocker", "status": "closed"})
        tickets = [blocker, ticket(2, blocked_by=[1])]
        layout = gd.compute_diagram_layout(tickets)
        card2 = next(c for col in layout.columns for c in col.cards if c.ticket.number == 2)
        self.assertEqual(len(card2.deps), 1)
        dep = card2.deps[0]
        self.assertEqual(dep.number, 1)
        self.assertEqual(dep.title, "Blocker")
        self.assertTrue(dep.done)

    def test_step_divider_between_plain_columns(self):
        tickets = [ticket(1), ticket(2, blocked_by=[1])]
        layout = gd.compute_diagram_layout(tickets)
        self.assertFalse(layout.columns[0].step_divider)
        self.assertEqual(layout.columns[0].dividers, [])
        self.assertTrue(layout.columns[1].step_divider)
        self.assertEqual(layout.columns[1].dividers, [])

    def test_chunk_divider_suppresses_step_divider(self):
        tickets = [ticket(1, chunk=1), ticket(2, chunk=2)]
        layout = gd.compute_diagram_layout(tickets)
        chunk2_column = layout.columns[1]
        self.assertFalse(chunk2_column.step_divider)
        self.assertEqual([d["label"] for d in chunk2_column.dividers], ["Chunk #2"])

    def test_counts_and_percentage(self):
        t1 = ticket(1)
        t1 = gd.Ticket(**{**t1.__dict__, "status": "closed"})
        tickets = [t1, ticket(2), ticket(3), ticket(4)]
        layout = gd.compute_diagram_layout(tickets)
        self.assertEqual(layout.done_count, 1)
        self.assertEqual(layout.total, 4)
        self.assertEqual(layout.pct, 25)

    def test_empty_tickets_gives_zero_percent(self):
        layout = gd.compute_diagram_layout([])
        self.assertEqual(layout.done_count, 0)
        self.assertEqual(layout.total, 0)
        self.assertEqual(layout.pct, 0)


class RenderCardTests(unittest.TestCase):
    def test_shows_sub_progress_text_when_present(self):
        t = ticket(1, sub_progress=(2, 5))
        card = gd.compute_diagram_layout([t]).columns[0].cards[0]
        html_out = gd.render_card(card)
        self.assertIn("2/5", html_out)

    def test_no_sub_progress_markup_when_absent(self):
        t = ticket(1)
        card = gd.compute_diagram_layout([t]).columns[0].cards[0]
        html_out = gd.render_card(card)
        self.assertNotIn("sub-progress", html_out)

    def test_sub_progress_class_present_for_styling(self):
        t = ticket(1, sub_progress=(0, 3))
        card = gd.compute_diagram_layout([t]).columns[0].cards[0]
        html_out = gd.render_card(card)
        self.assertIn('class="sub-progress"', html_out)

    def test_shows_a_badge_per_label(self):
        t = ticket(1, labels=["bug", "ui"])
        card = gd.compute_diagram_layout([t]).columns[0].cards[0]
        html_out = gd.render_card(card)
        self.assertIn("bug", html_out)
        self.assertIn("ui", html_out)
        self.assertEqual(html_out.count("label-badge"), 2)

    def test_no_label_markup_when_no_labels(self):
        t = ticket(1)
        card = gd.compute_diagram_layout([t]).columns[0].cards[0]
        html_out = gd.render_card(card)
        self.assertNotIn("label-badge", html_out)
        self.assertNotIn('class="labels"', html_out)


class LabelBadgeCssTests(unittest.TestCase):
    """Regression: rotate(-90deg) on a box stretched to the card's full
    height swaps its bounding-box width/height, so the rotated badge grows
    very wide and spills horizontally into the card body. writing-mode:
    vertical-rl lays the text out vertically without touching the box's
    own dimensions, so that must be what .label-badge uses."""

    def _css_rule(self):
        html_out = gd.build_html(
            [ticket(1)], known_repos=["a/b"], active_repo="a/b", offer_refresh=True
        )
        start = html_out.index(".label-badge {")
        end = html_out.index("}", start)
        return html_out[start : end + 1]

    def test_uses_writing_mode_not_rotate_minus_90(self):
        rule = self._css_rule()
        self.assertIn("writing-mode: vertical-rl", rule)
        self.assertNotIn("rotate(-90deg)", rule)

    def test_clips_its_own_overflow(self):
        """A label longer than the card is tall (e.g. 'wayfinder:map') must
        not paint past the badge's own box — clipping only on the .labels
        ancestor doesn't reliably clip a rotated/transformed child."""
        rule = self._css_rule()
        self.assertIn("overflow: hidden", rule)


class ServerOfflineBannerTests(unittest.TestCase):
    """#17: fetch() rejection (server unreachable) on any of the three API
    actions must reveal the persistent header banner; a resolved non-OK
    response (server running, answering with an error) must not."""

    def _html(self, **kwargs):
        t = ticket(1)
        defaults = dict(known_repos=["a/b"], active_repo="a/b", offer_refresh=True)
        defaults.update(kwargs)
        return gd.build_html([t], **defaults)

    def test_banner_markup_present_and_hidden_by_default(self):
        html_out = self._html()
        self.assertIn('id="server-offline-banner"', html_out)
        self.assertIn('class="server-offline-banner" hidden', html_out)

    def test_banner_present_even_without_repo_switcher_or_refresh(self):
        # ticket-status toggle is always rendered, so the banner must be too.
        html_out = self._html(known_repos=None, active_repo=None, offer_refresh=False)
        self.assertIn('id="server-offline-banner"', html_out)

    def test_all_three_actions_call_show_banner_on_catch(self):
        html_out = self._html()
        self.assertIn("window.showServerOfflineBanner = function", html_out)
        self.assertEqual(html_out.count("showServerOfflineBanner()"), 3)

    def test_repo_switch_catch_shows_banner_before_ok_check(self):
        html_out = self._html()
        script = gd.render_repo_switcher(["a/b"], "a/b")
        self.assertIn("} catch (err) {\n            window.showServerOfflineBanner();", script)
        self.assertIn("if (!res.ok) {", script)

    def test_refresh_catch_shows_banner_and_non_ok_does_not(self):
        script = gd.render_refresh_button()
        self.assertIn("} catch (err) {\n            window.showServerOfflineBanner();", script)
        # non-OK branch (server running, answered with an error) must not
        # call the banner helper.
        not_ok_branch = script.split("if (!res.ok) {")[1].split("return;")[0]
        self.assertNotIn("showServerOfflineBanner", not_ok_branch)

    def test_ticket_status_toggle_catch_shows_banner(self):
        html_out = self._html()
        self.assertIn(
            "} catch (err) {\n        window.showServerOfflineBanner();",
            html_out,
        )


if __name__ == "__main__":
    unittest.main()
