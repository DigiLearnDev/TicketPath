# Ticket Path v2 — Spec

Zdroj: grill-me relace 2026-08-30. Určeno jako vstup pro pozdější `/to-tickets`.

## Kontext / motivace

Současný nástroj (`generate_diagram.py`) je čistě statický: přečte `tickets.txt`
(ručně kurátorovaný agentem podle GitHubu), spočítá topologické vrstvy podle
`blocked_by` a vygeneruje samostatný offline `diagram.html`. Žádná
persistence, žádné síťové volání z prohlížeče, žádná interaktivita kromě
hover-zvýraznění.

Cíl v2: udělat z toho živý nástroj s manuální kontrolou nad rozvržením,
tlačítkem pro obnovu z GitHubu, a rychlou cestou "chci na tomhle ticketu
začít pracovat" → zkopírovat referenci a hodit ji agentovi.

**Repo zůstává striktně read-only** (`DigiLearnDev/DigiLearn`, `gh` CLI) —
nic z tohoto nástroje nikdy nezapisuje na GitHub. Tento princip platí
beze změny i ve v2.

## Architektura

Přechod ze statického souboru na **lokální Python HTTP server**:

- `server.py` (stdlib `http.server` nebo podobně lehké, žádné nové
  závislosti) poslouchá na `http://localhost:8765`, běží nepřetržitě na
  pozadí dokud ho uživatel ručně nezavře.
- `Aktualizovat-diagram.bat` spustí `server.py` a otevře prohlížeč na
  `http://localhost:8765`.
- Server servíruje vygenerovaný diagram (žádné CDN, žádné externí JS/CSS —
  tahle vlastnost zůstává i po přechodu na server) a obsluhuje pár
  JSON API endpointů pro interaktivní funkce (viz níže).
- GitHub komunikace: server spouští `gh` CLI jako subprocess (`gh api
  graphql` s read-only query), spoléhá na už přihlášené `gh` na stroji.
  Žádný token se nikam neukládá ani nezapisuje do souborů diagramu.

## Rozsah dat

Žádný hard-coded rozsah čísel ticketů (dřívější #19–39 padá). Refresh vždy
natáhne **všechny** issues z repa (open i closed, od #1 výš). Oddělení
"staré" a "nové" fáze projektu řeší uživatel ručně pomocí phase dividerů
(viz níže), ne skript.

## Nová perzistence

Nový soubor `diagram-state.json` vedle `tickets.txt`. Čistě lokální stav,
nikdy se neposílá na GitHub, přežívá regeneraci diagramu.

```json
{
  "tickets": {
    "<číslo>": {
      "manual_layer": <int|null>,
      "status_override": "open" | "closed" | null
    }
  },
  "phase_dividers": [
    { "id": "<uuid>", "label": "<text>", "after_ticket": <číslo> }
  ]
}
```

## Funkce

### 1. Drag & drop karty mezi sloupci
Přetažení karty do jiného sloupce trvale přepíše vypočtenou vrstvu
(`manual_layer` v `diagram-state.json`), přežije i regeneraci. Pokud další
GitHub refresh přepočítá vrstvu jinak, než kam je karta ručně umístěná
(override je "zastaralý" vůči nově vypočtené hodnotě), karta dostane
**přerušovaný okraj + tooltip** vysvětlující, kam by normálně patřila. Žádná
trvalá "pin" ikonka — jen dashed border, jen když je override v rozporu s
aktuálním výpočtem.

### 2. Ruční přepnutí stavu (klik na status indikátor v rohu karty)
Klik přepne open ↔ closed a **zapíše přímo do `tickets.txt`** (`status:`
pole). Není to oddělený override — je to "poznámka do doby, než přijde
pravda z GitHubu": další refresh z GitHubu tuhle ruční hodnotu přepíše
podle skutečného stavu issue.

### 3. Tlačítko "Aktualizovat" (GitHub refresh)
Server jedním GraphQL dotazem (`gh api graphql`) natáhne pro celé repo:
`number, title, state, body, subIssues{number, state}`.

- `blocked_by` se parsuje regexem hledajícím "Blocked by #N" (a
  ekvivalenty) v `body` — zachovává současnou konvenci z `tickets.txt`
  (viz komentář v souboru, řádky 20–24).
- Sub-issues (GitHub nativní hierarchie, potvrzeno reálně používané v repu
  — např. #29 → #36,#37,#38,#39; #23 → #32) se promítnou jako
  **informativní odznak** na kartě dítěte, např. "část #29". Nepromítá se
  do `blocked_by` ani do výpočtu vrstev/layoutu — čistě vizuální.
- Nově objevené tickety (čísla, která v `tickets.txt` před refreshem
  nebyla) dostanou **"new" odznak**, který zmizí automaticky při dalším
  refreshi (žádný ruční dismiss, žádný další stav k persistování).
- Refresh přepíše `tickets.txt` (nová/aktualizovaná data z GitHubu) a
  zachovává `diagram-state.json` (override vrstvy, phase dividery) beze
  změny — pouze označí override jako "zastaralý" vizuálně, pokud je to
  relevantní (viz bod 1).

### 4. Phase divider
Svislá čára oddělující fáze projektu (např. "stará fáze" vs "generation
core"), libovolný počet.

- **Vazba**: na konkrétní ticket (`after_ticket`), ne na index sloupce.
  Vykreslí se v mezeře hned za aktuálním sloupcem toho ticketu — zůstává
  smysluplná i po přepočtu vln při refreshi.
- **Vytvoření**: malá ikonka čáry v headeru (vedle progress baru); tažení
  ikonky myší vytvoří novou instanci divideru ("vytažení z krabice").
- **Přesun**: chycení existující čáry a drop na jiný ticket ji tam
  přiváže (mění `after_ticket`).
- **Smazání**: během tažení se ikonka mění na vzhled "kontejneru/koše" —
  puštění v tomto stavu čáru smaže.
- **Label**: klik na text čáry = inline edit (contenteditable). Nová
  čára vzniká rovnou v edit módu s placeholder textem "nová fáze".

### 5. Sub-sloupce (přetečení sloupce)
Když karty v jednom sloupci (vlně) přesáhnou výšku `diagram-wrap`
kontejneru, další karty pokračují ve **vedlejším sub-sloupci** napravo, s
malým odsazením (menším než mezera mezi skutečnými vlnami/sloupci) —
signalizuje "je to pořád tenhle sloupec, jen pokračuje". Karty se řadí
dál podle čísla ticketu napříč sub-sloupci. Tím roste celková šířka
diagramu — řeší horizontální scroll (bod 6).

### 6. Scroll
Prostý wheel nad `diagram-wrap` = horizontální scroll (žádné Ctrl
potřeba — změna oproti v1, kde Ctrl+wheel dělal totéž). Žádný speciální
vertikální scroll — sub-sloupce (bod 5) zajišťují, že layout vždy respektuje
výšku okna.

### 7. Dvojklik na kartu → kopírování reference
Zkopíruje do schránky přesně:

```
DigiLearn ticket #<číslo>
```

(mezery mezi slovy, žádné pomlčky ani jiné oddělovače). Žádné odvozování
typu ticketu ani mapování na konkrétní skill — to řeší až navazující
`/Implement`-style command mimo scope tohoto nástroje.

## Explicitně mimo scope této verze

- Typové rozlišení ticketů / automatické mapování na Matt Pococka skillset
  (GitHub issues nejsou typově rozlišené; řeší se to jinde, ne v diagramu).
- Zápis čehokoliv zpět na GitHub.
- Autentizace/token management nad rámec už přihlášeného `gh` CLI.

## Otevřené technické detaily pro `/to-tickets`

Toto je spec záměru a chování, ne implementační plán. `/to-tickets` má
rozhodnout dělení na tickety, přesné API kontrakty serveru (endpointy,
request/response tvar), a pořadí implementace (např. tracer-bullet: server
+ read-only zobrazení všech ticketů jako první slice, pak drag&drop, pak
phase divider, pak refresh, pak clipboard — ale to je na uvážení
`/to-tickets`, ne fixované touto specifikací).
