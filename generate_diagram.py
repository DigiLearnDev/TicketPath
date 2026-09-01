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
from datetime import datetime
from pathlib import Path

import repo_store

HERE = Path(__file__).resolve().parent
OUTPUT_FILE = HERE / "diagram.html"


def parse_tickets(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()

    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("#"):
            continue
        if stripped == "":
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(stripped)
    if current:
        blocks.append(current)

    tickets = []
    for block in blocks:
        data: dict[str, str] = {}
        for line in block:
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            data[key.strip()] = value.strip()
        if "ticket" not in data:
            continue

        number = int(data["ticket"])
        title = data.get("title", f"#{number}")
        status = data.get("status", "open").strip().lower()
        blocked_raw = data.get("blocked_by", "none").strip().lower()
        if blocked_raw in ("none", ""):
            blocked_by: list[int] = []
        else:
            blocked_by = [int(x.strip()) for x in blocked_raw.split(",") if x.strip()]

        part_of_raw = data.get("part_of", "").strip()
        part_of = int(part_of_raw) if part_of_raw else None

        chunk_raw = data.get("chunk", "").strip()
        chunk = int(chunk_raw) if chunk_raw else None

        sub_progress_raw = data.get("sub_progress", "").strip()
        if sub_progress_raw and "/" in sub_progress_raw:
            done_str, _, total_str = sub_progress_raw.partition("/")
            sub_progress = (int(done_str.strip()), int(total_str.strip()))
        else:
            sub_progress = None

        tickets.append(
            {
                "number": number,
                "title": title,
                "status": status,
                "blocked_by": blocked_by,
                "part_of": part_of,
                "chunk": chunk,
                "sub_progress": sub_progress,
            }
        )

    tickets.sort(key=lambda t: t["number"])
    return tickets


def compute_layers(
    tickets: list[dict], extra_blocked_by: dict[int, list[int]] | None = None
) -> dict[int, int]:
    """Layer = longest blocked_by chain to a ticket with no blockers. `extra_blocked_by`
    adds synthetic dependencies (e.g. a chunk floor) into the same recursion, so they
    propagate exactly like a real blocked_by edge — including transitively, to tickets
    that depend on a ticket carrying an extra dependency of its own."""
    extra_blocked_by = extra_blocked_by or {}
    by_number = {t["number"]: t for t in tickets}
    layer_cache: dict[int, int] = {}

    def layer_of(number: int, stack: set[int]) -> int:
        if number in layer_cache:
            return layer_cache[number]
        if number in stack:
            # Cyklus v datech (nemel by nastat) — radeji nespadnout.
            return 0
        stack = stack | {number}
        blockers = by_number.get(number, {}).get("blocked_by", []) + extra_blocked_by.get(number, [])
        if not blockers:
            result = 0
        else:
            result = 1 + max(
                layer_of(b, stack) for b in blockers if b in by_number
            )
        layer_cache[number] = result
        return result

    return {t["number"]: layer_of(t["number"], set()) for t in tickets}


def effective_layers_and_dividers(tickets: list[dict]) -> tuple[dict[int, int], list[dict]]:
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
    {"label": "Chunk #N", "after_layer": <column index>} — the column after
    which its line should render, i.e. the rightmost column of that chunk.
    """
    by_chunk: dict[int, list[int]] = {}
    for t in tickets:
        if t.get("chunk") is not None:
            by_chunk.setdefault(t["chunk"], []).append(t["number"])

    synthetic_blockers: dict[int, list[int]] = {}
    lower_chunks_union: list[int] = []
    for chunk in sorted(by_chunk):
        for number in by_chunk[chunk]:
            synthetic_blockers[number] = lower_chunks_union
        lower_chunks_union = lower_chunks_union + by_chunk[chunk]

    effective = compute_layers(tickets, synthetic_blockers)

    dividers = [
        {"label": f"Chunk #{chunk}", "after_layer": max(effective[n] for n in by_chunk[chunk])}
        for chunk in sorted(by_chunk)
    ]

    return effective, dividers


def ticket_state(ticket: dict, by_number: dict[int, dict]) -> str:
    if ticket["status"] == "closed":
        return "done"
    blockers = ticket["blocked_by"]
    if not blockers:
        return "ready"
    if all(by_number[b]["status"] == "closed" for b in blockers if b in by_number):
        return "ready"
    return "blocked"


ICON_OPEN = """<svg viewBox="0 0 20 20" class="icon"><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="2"/></svg>"""
ICON_DONE = """<svg viewBox="0 0 20 20" class="icon"><circle cx="10" cy="10" r="9" fill="currentColor"/><path d="M6 10.3l2.5 2.5L14.5 7" fill="none" stroke="var(--bg)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>"""


def render_card(
    ticket: dict,
    state: str,
    by_number: dict[int, dict],
    is_new: bool = False,
) -> str:
    icon = ICON_DONE if state == "done" else ICON_OPEN
    title = html.escape(ticket["title"])
    number = ticket["number"]

    blockers = ticket["blocked_by"]
    dep_html = ""
    if blockers:
        parts = []
        for b in blockers:
            b_title = by_number.get(b, {}).get("title", "")
            b_done = by_number.get(b, {}).get("status") == "closed"
            cls = "dep done" if b_done else "dep"
            parts.append(f'<span class="{cls}" title="{html.escape(b_title)}">#{b}</span>')
        dep_html = f'<div class="deps">čeká na: {" ".join(parts)}</div>'

    part_of_html = ""
    if ticket.get("part_of") is not None:
        part_of_html = f'<div class="part-of">část #{ticket["part_of"]}</div>'

    sub_progress_html = ""
    sub_progress = ticket.get("sub_progress")
    if sub_progress is not None:
        sub_done, sub_total = sub_progress
        sub_progress_html = f'<div class="sub-progress">{sub_done}/{sub_total}</div>'

    badge = ""
    if is_new:
        badge = '<span class="badge new">nové</span>'
    elif state == "ready":
        badge = '<span class="badge ready">připraveno</span>'
    elif state == "blocked":
        badge = '<span class="badge blocked">blokováno</span>'

    blocked_by_attr = ",".join(str(b) for b in blockers)

    return f"""
      <article class="card {state}" data-ticket="{number}" data-blocked-by="{blocked_by_attr}">
        <div class="card-top">
          <button type="button" class="status-icon" data-toggle-status="{number}" title="Přepnout stav">{icon}</button>
          <span class="num">#{number}</span>
          {badge}
        </div>
        <h3>{title}</h3>
        {part_of_html}
        {sub_progress_html}
        {dep_html}
      </article>
    """


def render_divider(divider: dict) -> str:
    label = html.escape(divider.get("label", ""))
    return f"""
      <div class="divider">
        <div class="divider-label">{label}</div>
        <div class="divider-line"></div>
      </div>
    """


def build_html(
    tickets: list[dict],
    header_extra: str = "",
    new_tickets: set[int] | None = None,
    repo_short_name: str = "DigiLearn",
) -> str:
    if new_tickets is None:
        new_tickets = set()

    by_number = {t["number"]: t for t in tickets}
    layers, dividers = effective_layers_and_dividers(tickets)

    max_layer = max(layers.values()) if layers else 0
    columns: list[list[dict]] = [[] for _ in range(max_layer + 1)]
    for t in tickets:
        columns[layers[t["number"]]].append(t)
    for col in columns:
        col.sort(key=lambda t: t["number"])

    done_count = sum(1 for t in tickets if t["status"] == "closed")
    total = len(tickets)
    pct = round(100 * done_count / total) if total else 0

    dividers_by_column: dict[int, list[dict]] = {}
    for d in dividers:
        dividers_by_column.setdefault(d["after_layer"], []).append(d)

    columns_html = []
    for i, col in enumerate(columns):
        if i > 0 and not dividers_by_column.get(i - 1):
            columns_html.append('<div class="step-divider"></div>')

        cards_html = "\n".join(
            render_card(
                t,
                ticket_state(t, by_number),
                by_number,
                t["number"] in new_tickets,
            )
            for t in col
        )
        columns_html.append(
            f"""
        <div class="column" data-layer="{i}">
          <div class="column-cards">{cards_html}</div>
        </div>
        """
        )
        for d in dividers_by_column.get(i, []):
            columns_html.append(render_divider(d))

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
    width: 260px;
    flex: none;
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 4px solid var(--border);
    border-radius: 10px;
    padding: 12px 14px;
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

  cards.forEach(card => {{
    card.addEventListener('dblclick', () => {{
      navigator.clipboard.writeText(`${{repoShortName}} ticket #${{card.dataset.ticket}}`);
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
      const res = await fetch('/api/ticket-status', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ ticket }}),
      }});
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
    tickets = parse_tickets(repo_store.tickets_path(state["active_repo"]))
    output = build_html(tickets, repo_short_name=repo_store.repo_short_name(state["active_repo"]))
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"Vygenerovano: {OUTPUT_FILE}")
    webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
