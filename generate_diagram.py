"""Generuje diagram.html ze stavu ticketu v tickets.txt a otevre ho v prohlizeci.

Pouziti:
    python generate_diagram.py

Zadne externi zavislosti (jen standardni knihovna), zadne sitove pozadavky —
vysledny diagram.html je 100% samostatny a funguje i offline.
"""
from __future__ import annotations

import html
import json
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import repo_store
import ticket_store
from ticket import Ticket

HERE = Path(__file__).resolve().parent
OUTPUT_FILE = HERE / "diagram.html"


def compute_layers(
    tickets: list[Ticket], extra_blocked_by: dict[int, list[int]] | None = None
) -> dict[int, int]:
    """Layer = longest blocked_by chain to a ticket with no blockers. `extra_blocked_by`
    adds synthetic dependencies (e.g. a chunk floor) into the same recursion, so they
    propagate exactly like a real blocked_by edge — including transitively, to tickets
    that depend on a ticket carrying an extra dependency of its own."""
    extra_blocked_by = extra_blocked_by or {}
    by_number = {t.number: t for t in tickets}
    layer_cache: dict[int, int] = {}

    def layer_of(number: int, stack: set[int]) -> int:
        if number in layer_cache:
            return layer_cache[number]
        if number in stack:
            # Cyklus v datech (nemel by nastat) — radeji nespadnout.
            return 0
        stack = stack | {number}
        blockers = (by_number[number].blocked_by if number in by_number else []) + extra_blocked_by.get(number, [])
        if not blockers:
            result = 0
        else:
            result = 1 + max(
                layer_of(b, stack) for b in blockers if b in by_number
            )
        layer_cache[number] = result
        return result

    return {t.number: layer_of(t.number, set()) for t in tickets}


def effective_layers_and_dividers(tickets: list[Ticket]) -> tuple[dict[int, int], list[dict]]:
    """Derives each ticket's effective layer from its `blocked_by` chain plus a
    floor implied by its `Chunk #N` label, and the phase dividers that floor
    implies.

    A ticket with chunk N is fed into `compute_layers` as if it were also
    `blocked_by` every ticket in every lower-numbered chunk that has tickets —
    a real edge in the same recursion, not a separate pass over already-computed
    values. That's what makes the push transitive: a plain ticket (no chunk of
    its own) that's `blocked_by` a chunk-pushed ticket picks up the push
    automatically, the same way it would pick up any other blocker's layer.
    Chunks with no tickets are skipped automatically (only chunk numbers that
    have tickets are iterated), so gaps in the numbering don't break the chain.

    Returns (effective_layer_by_number, dividers), where each divider is
    {"label": "Chunk #N", "before_layer": <column index>} — the column before
    which its line should render, i.e. the leftmost column of that chunk.
    """
    by_chunk: dict[int, list[int]] = {}
    for t in tickets:
        if t.chunk is not None:
            by_chunk.setdefault(t.chunk, []).append(t.number)

    synthetic_blockers: dict[int, list[int]] = {}
    lower_chunks_union: list[int] = []
    for chunk in sorted(by_chunk):
        for number in by_chunk[chunk]:
            synthetic_blockers[number] = lower_chunks_union
        lower_chunks_union = lower_chunks_union + by_chunk[chunk]

    effective = compute_layers(tickets, synthetic_blockers)

    dividers = [
        {"label": f"Chunk #{chunk}", "before_layer": min(effective[n] for n in by_chunk[chunk])}
        for chunk in sorted(by_chunk)
    ]

    return effective, dividers


def ticket_state(ticket: Ticket, by_number: dict[int, Ticket]) -> str:
    if ticket.status == "closed":
        return "done"
    blockers = ticket.blocked_by
    if not blockers:
        return "ready"
    if all(by_number[b].status == "closed" for b in blockers if b in by_number):
        return "ready"
    return "blocked"


ICON_OPEN = """<svg viewBox="0 0 20 20" class="icon"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="2"/></svg>"""
ICON_DONE = """<svg viewBox="0 0 20 20" class="icon"><circle cx="10" cy="10" r="9" fill="currentColor"/><path d="M6 10.3l2.5 2.5L14.5 7" fill="none" stroke="var(--bg)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

BADGE_LABELS = {"new": "nové", "ready": "připraveno", "blocked": "blokováno"}


@dataclass(frozen=True)
class Dep:
    number: int
    title: str
    done: bool


@dataclass(frozen=True)
class CardLayout:
    ticket: Ticket
    state: str
    badge: str | None  # "new" | "ready" | "blocked" | None — key into BADGE_LABELS
    deps: list[Dep] = field(default_factory=list)


@dataclass(frozen=True)
class ColumnLayout:
    index: int
    dividers: list[dict]
    step_divider: bool
    cards: list[CardLayout] = field(default_factory=list)


@dataclass(frozen=True)
class DiagramLayout:
    columns: list[ColumnLayout]
    done_count: int
    total: int
    pct: int


def compute_diagram_layout(
    tickets: list[Ticket], new_tickets: set[int] | None = None
) -> DiagramLayout:
    """Pure function: turns tickets into the data description the template
    renders — column assignment, phase dividers, per-card state/badge/deps,
    and the summary counts. No HTML."""
    if new_tickets is None:
        new_tickets = set()

    by_number = {t.number: t for t in tickets}
    layers, dividers = effective_layers_and_dividers(tickets)

    max_layer = max(layers.values()) if layers else 0
    columns: list[list[Ticket]] = [[] for _ in range(max_layer + 1)]
    for t in tickets:
        columns[layers[t.number]].append(t)
    for col in columns:
        col.sort(key=lambda t: t.number)

    dividers_by_column: dict[int, list[dict]] = {}
    for d in dividers:
        dividers_by_column.setdefault(d["before_layer"], []).append(d)

    column_layouts: list[ColumnLayout] = []
    for i, col in enumerate(columns):
        column_dividers = dividers_by_column.get(i, [])
        step_divider = not column_dividers and i > 0

        cards: list[CardLayout] = []
        for t in col:
            state = ticket_state(t, by_number)
            is_new = t.number in new_tickets
            if is_new:
                badge = "new"
            elif state == "ready":
                badge = "ready"
            elif state == "blocked":
                badge = "blocked"
            else:
                badge = None

            deps = [
                Dep(
                    number=b,
                    title=by_number[b].title if b in by_number else "",
                    done=by_number[b].status == "closed" if b in by_number else False,
                )
                for b in t.blocked_by
            ]
            cards.append(CardLayout(ticket=t, state=state, badge=badge, deps=deps))

        column_layouts.append(
            ColumnLayout(
                index=i,
                dividers=column_dividers,
                step_divider=step_divider,
                cards=cards,
            )
        )

    done_count = sum(1 for t in tickets if t.status == "closed")
    total = len(tickets)
    pct = round(100 * done_count / total) if total else 0

    return DiagramLayout(columns=column_layouts, done_count=done_count, total=total, pct=pct)


def render_card(card: CardLayout) -> str:
    ticket = card.ticket
    icon = ICON_DONE if card.state == "done" else ICON_OPEN
    title = html.escape(ticket.title)
    number = ticket.number

    dep_html = ""
    if card.deps:
        parts = [
            f'<span class="{"dep done" if dep.done else "dep"}" title="{html.escape(dep.title)}">#{dep.number}</span>'
            for dep in card.deps
        ]
        dep_html = f'<div class="deps">čeká na: {" ".join(parts)}</div>'

    part_of_html = ""
    if ticket.part_of is not None:
        part_of_html = f'<div class="part-of">část #{ticket.part_of}</div>'

    sub_progress_html = ""
    sub_progress = ticket.sub_progress
    if sub_progress is not None:
        sub_done, sub_total = sub_progress
        sub_progress_html = f'<div class="sub-progress">{sub_done}/{sub_total}</div>'

    labels_html = ""
    if ticket.labels:
        label_spans = "".join(
            f'<span class="label-badge">{html.escape(label)}</span>' for label in ticket.labels
        )
        labels_html = f'<div class="labels">{label_spans}</div>'

    badge = (
        f'<span class="badge {card.badge}">{BADGE_LABELS[card.badge]}</span>'
        if card.badge
        else ""
    )

    blocked_by_attr = ",".join(str(dep.number) for dep in card.deps)

    return f"""
      <article class="card {card.state}" data-ticket="{number}" data-blocked-by="{blocked_by_attr}">
        <div class="card-top">
          <button type="button" class="status-icon" data-toggle-status="{number}" title="Přepnout stav">{icon}</button>
          <span class="num">#{number}</span>
          {badge}
        </div>
        <h3>{title}</h3>
        {part_of_html}
        {sub_progress_html}
        {labels_html}
        {dep_html}
      </article>
    """


def render_repo_switcher(known_repos: list[str], active_repo: str) -> str:
    options = "\n".join(
        f'<option value="{html.escape(repo)}"{" selected" if repo == active_repo else ""}>{html.escape(repo)}</option>'
        for repo in known_repos
    )
    return f"""
    <div class="repo-switcher">
      <label for="repo-select">Repo</label>
      <select id="repo-select">{options}</select>
      <button type="button" id="repo-add-btn" title="Přidat repo (owner/repo)">+ nové repo</button>
    </div>
    <script>
      (function() {{
        const select = document.getElementById('repo-select');
        const addBtn = document.getElementById('repo-add-btn');

        async function switchRepo(repo) {{
          let res;
          try {{
            res = await fetch('/api/repos', {{
              method: 'POST',
              headers: {{ 'Content-Type': 'application/json' }},
              body: JSON.stringify({{ repo }}),
            }});
          }} catch (err) {{
            window.showServerOfflineBanner();
            return;
          }}
          if (!res.ok) {{
            const body = await res.json().catch(() => ({{}}));
            alert(body.error || 'Nepodařilo se přepnout repo.');
            return;
          }}
          window.location.reload();
        }}

        select.addEventListener('change', () => switchRepo(select.value));
        addBtn.addEventListener('click', () => {{
          const repo = window.prompt('Nové repo (owner/repo):');
          if (repo && repo.trim()) switchRepo(repo.trim());
        }});
      }})();
    </script>
    """


def render_refresh_button() -> str:
    return """
    <div class="refresh-row">
      <button type="button" id="refresh-btn">Aktualizovat</button>
      <span id="refresh-status" class="refresh-status"></span>
    </div>
    <script>
      (function() {
        const btn = document.getElementById('refresh-btn');
        const status = document.getElementById('refresh-status');
        btn.addEventListener('click', async () => {
          btn.disabled = true;
          status.textContent = 'Aktualizuji z GitHubu…';
          let res;
          try {
            res = await fetch('/api/refresh', { method: 'POST' });
          } catch (err) {
            window.showServerOfflineBanner();
            status.textContent = 'Server není dostupný.';
            btn.disabled = false;
            return;
          }
          if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            status.textContent = body.error || 'Aktualizace selhala.';
            btn.disabled = false;
            return;
          }
          sessionStorage.setItem('tt-toast-refreshed', '1');
          window.location.reload();
        });
      })();
    </script>
    """


def render_header(known_repos: list[str] | None, active_repo: str | None, offer_refresh: bool) -> str:
    """Draws the entire header extras area (repo switcher + refresh button) so
    that its HTML, CSS classes and scripts live in one module. `known_repos`
    empty/None skips the switcher; `offer_refresh=False` skips the button —
    that's how a static, serverless export (generate_diagram.py's own main())
    renders no header extras at all."""
    parts = []
    if known_repos:
        parts.append(render_repo_switcher(known_repos, active_repo or ""))
    if offer_refresh:
        parts.append(render_refresh_button())
    return "".join(parts)


def render_divider(divider: dict) -> str:
    label = html.escape(divider.get("label", ""))
    return f"""
      <div class="divider">
        <div class="divider-label">{label}</div>
        <div class="divider-line"></div>
      </div>
    """


def build_html(
    tickets: list[Ticket],
    new_tickets: set[int] | None = None,
    repo_short_name: str = "DigiLearn",
    known_repos: list[str] | None = None,
    active_repo: str | None = None,
    offer_refresh: bool = False,
) -> str:
    layout = compute_diagram_layout(tickets, new_tickets)
    header_extra = render_header(known_repos, active_repo, offer_refresh)

    columns_html = []
    for col in layout.columns:
        if col.dividers:
            for d in col.dividers:
                columns_html.append(render_divider(d))
        elif col.step_divider:
            columns_html.append('<div class="step-divider"></div>')

        cards_html = "\n".join(render_card(c) for c in col.cards)
        columns_html.append(
            f"""
        <div class="column" data-layer="{col.index}">
          <div class="column-cards">{cards_html}</div>
        </div>
        """
        )

    done_count = layout.done_count
    total = layout.total
    pct = layout.pct

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    return f"""<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<title>DigiLearn — stav ticketů</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --bg: #f7f7f8;
    --panel: #ffffff;
    --text: #1c1c1f;
    --muted: #6b6b74;
    --border: #e3e3e7;
    --green: #16a34a;
    --green-bg: #eafaf0;
    --amber: #b45309;
    --amber-bg: #fff6e6;
    --gray-bg: #f1f1f3;
    --accent: #4f46e5;
    --line-open: #c8c8ce;
    --line-done: #7dd3a8;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #17171a;
      --panel: #212126;
      --text: #eceaf0;
      --muted: #98979f;
      --border: #34343b;
      --green: #34d399;
      --green-bg: #123424;
      --amber: #f5b25b;
      --amber-bg: #3a2b0e;
      --gray-bg: #2a2a30;
      --accent: #8b83f7;
      --line-open: #46454e;
      --line-done: #2f6b4f;
    }}
  }}
  * {{ box-sizing: border-box; }}
  .toast {{
    position: fixed;
    bottom: 24px;
    left: 50%;
    transform: translate(-50%, 12px);
    background: var(--text);
    color: var(--bg);
    padding: 10px 18px;
    border-radius: 8px;
    font-size: 13px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.2);
    opacity: 0;
    pointer-events: none;
    transition: opacity 0.2s ease, transform 0.2s ease;
    z-index: 1000;
    max-width: 80vw;
    text-align: center;
  }}
  .toast.visible {{
    opacity: 1;
    transform: translate(-50%, 0);
  }}
  html, body {{
    height: 100%;
    overflow: hidden;
  }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    padding: 32px 24px 24px;
    display: flex;
    flex-direction: column;
  }}
  header {{
    margin: 0 0 28px;
    width: 100%;
    flex: none;
  }}
  h1 {{
    font-size: 22px;
    margin: 0 0 4px;
  }}
  .meta {{
    color: var(--muted);
    font-size: 13px;
  }}
  .server-offline-banner {{
    margin-top: 12px;
    padding: 8px 12px;
    border-radius: 6px;
    background: var(--amber-bg);
    color: var(--amber);
    font-size: 13px;
    font-weight: 600;
  }}
  .server-offline-banner[hidden] {{
    display: none;
  }}
  .repo-switcher {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 12px;
    font-size: 13px;
    color: var(--muted);
  }}
  .repo-switcher select {{
    font: inherit;
    color: var(--text);
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 6px;
  }}
  .repo-switcher button {{
    font: inherit;
    color: var(--accent);
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 8px;
    cursor: pointer;
  }}
  .refresh-row {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 12px;
    font-size: 13px;
  }}
  .refresh-row button {{
    font: inherit;
    color: var(--accent);
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 3px 8px;
    cursor: pointer;
  }}
  .refresh-row button:disabled {{
    opacity: 0.5;
    cursor: default;
  }}
  .refresh-status {{
    color: var(--muted);
  }}
  .progress-row {{
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 14px;
    max-width: 480px;
  }}
  .progress-track {{
    flex: 1;
    height: 8px;
    border-radius: 4px;
    background: var(--gray-bg);
    overflow: hidden;
  }}
  .progress-fill {{
    height: 100%;
    background: var(--green);
    border-radius: 4px;
  }}
  .progress-text {{
    font-size: 13px;
    color: var(--muted);
    white-space: nowrap;
  }}
  .legend {{
    display: flex;
    gap: 20px;
    margin-top: 16px;
    font-size: 13px;
    color: var(--muted);
    flex-wrap: wrap;
  }}
  .legend span {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }}
  .legend .icon {{ width: 14px; height: 14px; }}
  .legend .swatch {{
    width: 10px; height: 10px; border-radius: 50%;
  }}
  .diagram-wrap {{
    position: relative;
    width: 100%;
    overflow-x: auto;
    overflow-y: hidden;
    flex: 1 1 auto;
    min-height: 0;
  }}
  .columns {{
    display: flex;
    align-items: flex-start;
    gap: 48px;
  }}
  .column {{
    min-width: 260px;
    flex-shrink: 0;
  }}
  .step-divider {{
    align-self: stretch;
    width: 1px;
    flex-shrink: 0;
    background: var(--border);
  }}
  .column-cards {{
    display: flex;
    flex-direction: column;
    flex-wrap: wrap;
    align-content: flex-start;
    row-gap: 20px;
    column-gap: 16px;
  }}
  .card {{
    position: relative;
    width: 260px;
    flex: none;
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 4px solid var(--border);
    border-radius: 10px;
    padding: 12px 28px 12px 14px;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
    transition: opacity 0.15s ease;
  }}
  .card.done {{ border-left-color: var(--green); }}
  .card.ready {{ border-left-color: var(--accent); }}
  .card.blocked {{ border-left-color: var(--border); }}
  .card.dim {{ opacity: 0.25; }}
  .card-top {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }}
  .status-icon {{
    display: inline-flex;
    color: var(--muted);
    background: none;
    border: none;
    padding: 0;
    margin: 0;
    cursor: pointer;
    border-radius: 50%;
  }}
  .status-icon:hover {{ color: var(--accent); }}
  .card.done .status-icon {{ color: var(--green); }}
  .card.ready .status-icon {{ color: var(--accent); }}
  .icon {{ width: 18px; height: 18px; }}
  .num {{
    font-size: 12px;
    font-weight: 600;
    color: var(--muted);
  }}
  .badge {{
    margin-left: auto;
    font-size: 10px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.03em;
    padding: 2px 7px;
    border-radius: 999px;
  }}
  .badge.ready {{ background: var(--green-bg); color: var(--green); }}
  .badge.blocked {{ background: var(--gray-bg); color: var(--muted); }}
  .badge.new {{ background: var(--amber-bg); color: var(--amber); }}
  .part-of {{
    font-size: 11px;
    color: var(--muted);
    margin: -2px 0 6px;
  }}
  .sub-progress {{
    font-size: 11px;
    color: var(--muted);
    margin: -2px 0 6px;
  }}
  .labels {{
    position: absolute;
    top: 4px;
    right: 4px;
    bottom: 4px;
    display: flex;
    flex-direction: row-reverse;
    align-items: stretch;
    margin: 0;
    border-radius: 6px;
    overflow: hidden;
  }}
  .label-badge {{
    writing-mode: vertical-rl;
    transform: rotate(180deg);
    width: 22px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10.5px;
    color: var(--muted);
    background: var(--gray-bg);
    padding: 0;
    white-space: nowrap;
  }}
  .card h3 {{
    font-size: 13.5px;
    line-height: 1.35;
    margin: 0 0 6px;
    font-weight: 600;
  }}
  .deps {{
    font-size: 11.5px;
    color: var(--muted);
  }}
  .dep {{
    display: inline-block;
    padding: 1px 5px;
    border-radius: 5px;
    background: var(--gray-bg);
    margin-right: 3px;
  }}
  .dep.done {{ background: var(--green-bg); color: var(--green); }}
  .divider {{
    display: flex;
    flex-direction: column;
    align-items: center;
    align-self: stretch;
    width: 2px;
    position: relative;
  }}
  .divider-label {{
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    white-space: nowrap;
    font-size: 11px;
    font-weight: 600;
    color: var(--muted);
    padding: 1px 4px;
    border-radius: 4px;
    min-width: 10px;
  }}
  .divider-line {{
    flex: 1;
    width: 2px;
    margin-top: 20px;
    background: repeating-linear-gradient(to bottom, var(--accent) 0 6px, transparent 6px 12px);
  }}
  footer {{
    margin: 32px 0 0;
    font-size: 12px;
    color: var(--muted);
  }}
</style>
</head>
<body>
<header>
  <div id="server-offline-banner" class="server-offline-banner" hidden>Server TicketTraceru neběží nebo není dostupný. Spusť ho znovu a obnov stránku — do té doby akce v diagramu nebudou fungovat.</div>
  <h1>DigiLearn — implementační tickety (#19–#31)</h1>
  <div class="meta">generation core · tracer-bullet rozpad specu #18 · vygenerováno {now}</div>
  {header_extra}
  <div class="progress-row">
    <div class="progress-track"><div class="progress-fill" style="width:{pct}%"></div></div>
    <div class="progress-text">{done_count} / {total} hotovo ({pct}%)</div>
  </div>
  <div class="legend">
    <span>{ICON_OPEN} otevřený</span>
    <span>{ICON_DONE} hotovo / uzavřeno</span>
    <span><span class="swatch" style="background:var(--accent)"></span> připraveno k práci (blokery hotové)</span>
    <span><span class="swatch" style="background:var(--border)"></span> čeká na blokery</span>
  </div>
</header>

<div class="diagram-wrap">
  <div class="columns">
    {''.join(columns_html)}
  </div>
</div>

<footer>
  Zdroj dat: tickets.txt (ruční aktualizace agentem podle GitHubu — DigiLearnDev/DigiLearn, jen čtení).
  Spusť Aktualizovat-diagram.bat po úpravě tickets.txt pro obnovu.
</footer>

<div id="toast" class="toast" role="status" aria-live="polite"></div>

<script>
  const diagramWrap = document.querySelector('.diagram-wrap');
  diagramWrap.addEventListener('wheel', (e) => {{
    e.preventDefault();
    diagramWrap.scrollLeft += e.deltaY;
  }}, {{ passive: false }});

  function sizeColumnCards() {{
    const wrapRect = diagramWrap.getBoundingClientRect();
    document.querySelectorAll('.column').forEach(col => {{
      col.style.width = 'auto';
      const el = col.querySelector('.column-cards');
      el.style.height = 'auto';
      el.style.width = 'auto';
    }});
    document.querySelectorAll('.column').forEach(col => {{
      const el = col.querySelector('.column-cards');
      const top = el.getBoundingClientRect().top - wrapRect.top + diagramWrap.scrollTop;
      const height = Math.max(diagramWrap.clientHeight - top, 0);
      el.style.height = height + 'px';
      // Nested column-direction flex-wrap doesn't reliably report its own
      // wrapped width, so measure the wrapped cards' real paint position
      // and pin the container to that instead.
      const cardsLeft = el.getBoundingClientRect().left;
      let maxRight = 0;
      el.querySelectorAll('.card').forEach(card => {{
        maxRight = Math.max(maxRight, card.getBoundingClientRect().right - cardsLeft);
      }});
      const width = Math.max(maxRight, 260);
      el.style.width = width + 'px';
      col.style.width = width + 'px';
    }});
  }}
  sizeColumnCards();
  window.addEventListener('resize', sizeColumnCards);

  const cards = Array.from(document.querySelectorAll('.card'));
  const repoShortName = {json.dumps(repo_short_name)};

  const offlineBanner = document.getElementById('server-offline-banner');
  window.showServerOfflineBanner = function() {{
    if (offlineBanner) offlineBanner.hidden = false;
  }};

  const toastEl = document.getElementById('toast');
  let toastTimer = null;
  function showToast(text) {{
    if (toastTimer) clearTimeout(toastTimer);
    toastEl.textContent = text;
    toastEl.classList.add('visible');
    toastTimer = setTimeout(() => toastEl.classList.remove('visible'), 2500);
  }}

  if (sessionStorage.getItem('tt-toast-refreshed')) {{
    sessionStorage.removeItem('tt-toast-refreshed');
    showToast('Aktualizováno');
  }}

  cards.forEach(card => {{
    card.addEventListener('dblclick', () => {{
      const copied = `${{repoShortName}} ticket #${{card.dataset.ticket}}`;
      navigator.clipboard.writeText(copied);
      showToast(`Zkopírováno: ${{copied}}`);
    }});
  }});

  cards.forEach(card => {{
    const own = card.dataset.ticket;
    const blockedBy = card.dataset.blockedBy ? card.dataset.blockedBy.split(',') : [];
    const highlight = new Set([own, ...blockedBy]);

    card.addEventListener('mouseenter', () => {{
      cards.forEach(c => {{
        if (!highlight.has(c.dataset.ticket)) c.classList.add('dim');
      }});
    }});
    card.addEventListener('mouseleave', () => {{
      cards.forEach(c => c.classList.remove('dim'));
    }});
  }});

  document.querySelectorAll('[data-toggle-status]').forEach(btn => {{
    btn.addEventListener('click', async (e) => {{
      e.preventDefault();
      e.stopPropagation();
      const ticket = Number(btn.dataset.toggleStatus);
      let res;
      try {{
        res = await fetch('/api/ticket-status', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ ticket }}),
        }});
      }} catch (err) {{
        window.showServerOfflineBanner();
        return;
      }}
      if (res.ok) {{
        window.location.reload();
      }} else {{
        const body = await res.json().catch(() => ({{}}));
        alert(body.error || 'Nepodařilo se přepnout stav.');
      }}
    }});
  }});

</script>
</body>
</html>
"""


def main() -> None:
    state = repo_store.load_app_state()
    tickets = ticket_store.load_tickets(repo_store.tickets_path(state["active_repo"]))
    output = build_html(tickets, repo_short_name=repo_store.repo_short_name(state["active_repo"]))
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"Vygenerovano: {OUTPUT_FILE}")
    webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
