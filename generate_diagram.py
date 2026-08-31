"""Generuje diagram.html ze stavu ticketu v tickets.txt a otevre ho v prohlizeci.

Pouziti:
    python generate_diagram.py

Zadne externi zavislosti (jen standardni knihovna), zadne sitove pozadavky —
vysledny diagram.html je 100% samostatny a funguje i offline.
"""
from __future__ import annotations

import html
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

        tickets.append(
            {
                "number": number,
                "title": title,
                "status": status,
                "blocked_by": blocked_by,
            }
        )

    tickets.sort(key=lambda t: t["number"])
    return tickets


def compute_layers(tickets: list[dict]) -> dict[int, int]:
    by_number = {t["number"]: t for t in tickets}
    layer_cache: dict[int, int] = {}

    def layer_of(number: int, stack: set[int]) -> int:
        if number in layer_cache:
            return layer_cache[number]
        if number in stack:
            # Cyklus v datech (nemel by nastat) — radeji nespadnout.
            return 0
        stack = stack | {number}
        blockers = by_number.get(number, {}).get("blocked_by", [])
        if not blockers:
            result = 0
        else:
            result = 1 + max(
                layer_of(b, stack) for b in blockers if b in by_number
            )
        layer_cache[number] = result
        return result

    return {t["number"]: layer_of(t["number"], set()) for t in tickets}


def effective_layers(
    tickets: list[dict], computed: dict[int, int], diagram_state: dict
) -> tuple[dict[int, int], dict[int, bool]]:
    """Applies manual_layer overrides from diagram-state.json over computed layers.

    Returns (effective_layer_by_number, is_stale_by_number). A ticket is "stale"
    when it has a manual override that no longer matches the freshly computed
    layer (e.g. after a blocked_by edit) — it still renders at its manual layer,
    just flagged.
    """
    overrides = diagram_state.get("tickets", {})
    effective: dict[int, int] = {}
    stale: dict[int, bool] = {}
    for t in tickets:
        number = t["number"]
        entry = overrides.get(str(number))
        manual = entry.get("manual_layer") if entry else None
        if manual is not None:
            effective[number] = manual
            stale[number] = manual != computed[number]
        else:
            effective[number] = computed[number]
            stale[number] = False
    return effective, stale


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
    computed_layer: int,
    is_stale: bool,
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

    badge = ""
    if state == "ready":
        badge = '<span class="badge ready">připraveno</span>'
    elif state == "blocked":
        badge = '<span class="badge blocked">blokováno</span>'

    blocked_by_attr = ",".join(str(b) for b in blockers)

    stale_cls = " stale-override" if is_stale else ""
    stale_title = (
        f' title="Vypočtená vrstva: Krok {computed_layer + 1}"' if is_stale else ""
    )

    return f"""
      <article class="card {state}{stale_cls}" draggable="true" data-ticket="{number}" data-blocked-by="{blocked_by_attr}"{stale_title}>
        <div class="card-top">
          <span class="status-icon">{icon}</span>
          <span class="num">#{number}</span>
          {badge}
        </div>
        <h3>{title}</h3>
        {dep_html}
      </article>
    """


def build_html(
    tickets: list[dict], header_extra: str = "", diagram_state: dict | None = None
) -> str:
    if diagram_state is None:
        diagram_state = {"tickets": {}, "phase_dividers": []}

    by_number = {t["number"]: t for t in tickets}
    computed = compute_layers(tickets)
    layers, stale = effective_layers(tickets, computed, diagram_state)

    max_layer = max(max(layers.values()), max(computed.values())) if layers else 0
    columns: list[list[dict]] = [[] for _ in range(max_layer + 1)]
    for t in tickets:
        columns[layers[t["number"]]].append(t)
    for col in columns:
        col.sort(key=lambda t: t["number"])

    done_count = sum(1 for t in tickets if t["status"] == "closed")
    total = len(tickets)
    pct = round(100 * done_count / total) if total else 0

    columns_html = []
    for i, col in enumerate(columns):
        cards_html = "\n".join(
            render_card(
                t,
                ticket_state(t, by_number),
                by_number,
                computed[t["number"]],
                stale[t["number"]],
            )
            for t in col
        )
        columns_html.append(
            f"""
        <div class="column" data-layer="{i}">
          <div class="column-label">Krok {i + 1}</div>
          <div class="column-cards">{cards_html}</div>
        </div>
        """
        )

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
    max-width: 1400px;
    margin: 0 auto 28px;
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
    max-width: 1400px;
    width: 100%;
    margin: 0 auto;
    overflow: auto;
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
  }}
  .column-label {{
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin-bottom: 12px;
  }}
  .column-cards {{
    display: flex;
    flex-direction: column;
    gap: 20px;
  }}
  .card {{
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
  .card.stale-override {{
    border-style: dashed;
    border-color: var(--amber);
  }}
  .card[draggable="true"] {{ cursor: grab; }}
  .card.dragging {{ opacity: 0.4; }}
  .column-cards.drag-over {{
    outline: 2px dashed var(--accent);
    outline-offset: 4px;
    border-radius: 8px;
  }}
  .card-top {{
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
  }}
  .status-icon {{ display: inline-flex; color: var(--muted); }}
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
  footer {{
    max-width: 1400px;
    margin: 32px auto 0;
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
    if (e.ctrlKey) {{
      e.preventDefault();
      diagramWrap.scrollLeft += e.deltaY;
    }}
  }}, {{ passive: false }});

  const cards = Array.from(document.querySelectorAll('.card'));

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

  cards.forEach(card => {{
    card.addEventListener('dragstart', (e) => {{
      card.classList.add('dragging');
      e.dataTransfer.setData('text/plain', card.dataset.ticket);
      e.dataTransfer.effectAllowed = 'move';
    }});
    card.addEventListener('dragend', () => {{
      card.classList.remove('dragging');
    }});
  }});

  document.querySelectorAll('.column-cards').forEach(zone => {{
    zone.addEventListener('dragover', (e) => {{
      e.preventDefault();
      e.dataTransfer.dropEffect = 'move';
      zone.classList.add('drag-over');
    }});
    zone.addEventListener('dragleave', () => {{
      zone.classList.remove('drag-over');
    }});
    zone.addEventListener('drop', async (e) => {{
      e.preventDefault();
      zone.classList.remove('drag-over');
      const ticket = e.dataTransfer.getData('text/plain');
      const layer = Number(zone.closest('.column').dataset.layer);
      if (!ticket) return;
      const res = await fetch('/api/ticket-layer', {{
        method: 'POST',
        headers: {{ 'Content-Type': 'application/json' }},
        body: JSON.stringify({{ ticket: Number(ticket), layer }}),
      }});
      if (res.ok) {{
        window.location.reload();
      }} else {{
        const body = await res.json().catch(() => ({{}}));
        alert(body.error || 'Nepodařilo se uložit přesun.');
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
    output = build_html(tickets)
    OUTPUT_FILE.write_text(output, encoding="utf-8")
    print(f"Vygenerovano: {OUTPUT_FILE}")
    webbrowser.open(OUTPUT_FILE.as_uri())


if __name__ == "__main__":
    main()
