# Dwarf Doctrine Pilot

Co-pilota e autopilota *human-in-the-loop* per **Dwarf Fortress (Steam) + DFHack**, basato su una **Dottrina condivisa**.

| Ciclo | Chi | Costo token | Cosa fa |
|---|---|---|---|
| Strategico | Tu + Gemini (`gemini-3.5-flash`, configurabile), **solo su richiesta** | < 2.000 token/chiamata | Discute la strategia e propone una patch della `FortressDoctrine`. Tu la approvi con **[Approva e Automatizza]** o la **[Rifiuti]**. |
| Operativo | Autopilota deterministico locale, ogni 5 s | 0 | Legge lo stato e applica la dottrina approvata: work order, magazzini, blueprint Quickfort nelle zone designate. |
| Emergenza | Regole locali | 0 | Ordini d'emergenza calcolati (giorni di cibo/bevande), poi **pausa del gioco e notifica a te**. Gemini **non** viene mai chiamato in automatico: solo se clicchi "Analizza con Gemini". |

![Produzione](docs/production.png)

## Avvio rapido

```bash
uv venv --python 3.11 .venv && source .venv/bin/activate   # oppure python3.11 -m venv .venv
pip install -e ".[dev]"
python main.py                       # GUI + fortezza simulata (mock) integrata
python main.py --lang en             # interfaccia in inglese (italiano predefinito)
python main.py --dry-run --speed 2   # pianifica senza eseguire, simulazione più veloce
```

Mock server separato (stesso protocollo JSON del futuro bridge DFHack):

```bash
python -m dfhack_bridge.mock_server --speed 0.5 --seed 7   # porta 5577
python main.py                                              # si collega al server se è attivo
```

Chat con Gemini: imposta `GEMINI_API_KEY` (o `GOOGLE_API_KEY`) nell'ambiente oppure in un file `.env` nella root del repo (ignorato da git): `GEMINI_API_KEY=...`. Senza chiave la chat usa un advisor offline a parole chiave (birra, cibo, magazzini, difesa) per provare il flusso di approvazione.

| Variabile | Default | |
|---|---|---|
| `DDP_LANG` | `it` | `it` / `en` |
| `GEMINI_API_KEY` | — | chiave Gemini (Free Tier) |
| `DDP_GEMINI_MODEL` | `gemini-3.5-flash` | `gemini-2.5-flash` non è più disponibile per le chiavi nuove |
| `DDP_BRIDGE` | `mock` | `mock` / `dfhack` (vedi sotto) |
| `DDP_DFHACK_RUN` | da `PATH` | percorso di `dfhack-run(.exe)`, nella cartella del gioco |
| `DDP_MOCK_PORT` | `5577` | |
| `DDP_DATA_DIR` | `./data` | dottrina approvata + storico versioni |

## Struttura

```
core/
  doctrine.py     schema Pydantic FortressDoctrine, DoctrinePatch, diff, storico approvazioni
  autopilot.py    motore deterministico: emergenza > produzione > magazzini > blueprint
  alarms.py       soglie cibo/bevande/stress/inattività/oggetti a terra/eventi critici
  advisor.py      Gemini (structured output, budget token e richieste) + advisor offline
  i18n.py         localizzazione IT (default) / EN
dfhack_bridge/
  base.py         interfaccia FortressBridge
  protocol.py     contratto dello stato (FortressState)
  mock_server.py  simulazione della fortezza nel tempo + server TCP JSON-lines
  mock_bridge.py  client TCP e bridge in-process
  dfhack_bridge.py  bridge DFHack reale (lettura via dfhack-run; azioni disattivate)
  dfhack_state.py   mappatura export grezzo DF -> FortressState
  lua/ddp_state.lua exporter Lua in sola lettura
  probe.py          diagnostica: ddp-dfhack-probe
blueprints/       file Quickfort CSV + catalog.json (dimensioni, materiali, effetti)
ui/               GUI PySide6 stile Dwarf Therapist (QTableView + QAbstractTableModel)
tests/            pytest (dottrina, autopilota, mock server, advisor, GUI offscreen)
```

## Interfaccia

- **Produzione & Work Orders**: scorte vs target dottrina (copertura a barre, stato colorato), ordini con origine (autopilota / utente / emergenza).
- **Magazzini & Logistica**: riempimento %, oggetti a terra, link ai laboratori, regola di dottrina.
- **Pianificazione & Blueprints**: coda dottrina con precondizioni, stato (pianificato / bloccato / scavo / costruzione / completato), zone designate.
- **Nani & Ruoli**: griglia stile Therapist con stress, attività e livelli di abilità per mansione.
- **Pannello Dottrina & Chat**: direttive attive con switch ON/OFF per modulo, chat Gemini, diff della proposta con Approva/Rifiuta, log in tempo reale.

Barra superiore: autopilota ON/OFF, dry-run, pausa/riprendi, analisi strategica (chiamata Gemini esplicita), simulazione eventi (mock), lingua.

## DFHack reale (sola lettura)

Con Dwarf Fortress + DFHack aperti e una fortezza caricata:

```
ddp-dfhack-probe --dfhack-run "C:\Program Files (x86)\Steam\steamapps\common\Dwarf Fortress\dfhack-run.exe"
```

Stampa un riepilogo e salva `ddp_probe/raw_state.json` (export grezzo) e `ddp_probe/state.json` (stato mappato). Poi la GUI sulla partita vera:

```
set DDP_BRIDGE=dfhack
set DDP_DFHACK_RUN=C:\...\Dwarf Fortress\dfhack-run.exe
python main.py
```

- Con DFHack la GUI **parte sempre in dry-run**: l'autopilota pianifica ma non esegue. Per attivare le azioni togli la spunta a "Simulazione" e confermi l'avviso (salva prima la partita).
- **Azioni** (`lua/ddp_actions.lua`): ordini di lavoro nel formato della libreria ordini di DFHack (`workorder`), annullamento ordini, pausa, link magazzino → laboratorio.
- L'autopilota annulla **solo gli ordini creati da lui**: li registra in `data/dfhack_orders.json`, per salvataggio e fortezza. Se l'id di un ordine passa a un altro lavoro (es. caricando un salvataggio vecchio) l'ordine non è più considerato suo.
- La **pausa d'emergenza** viene eseguita anche in dry-run: è innocua ed è la regola di base.
- **Blueprint Quickfort** (Step 2C), in due fasi, ognuna è un'azione registrata nel log:
  1. *scavo*: la sezione `#dig` viene designata a partire dall'angolo in alto a sinistra della zona `DDP` (il blueprint deve starci, altrimenti viene rifiutato);
  2. *costruzione*: quando tutte le caselle da scavare sono aperte, vengono piazzate le sezioni `#build` (mobili, laboratori) e `#place` (magazzini).
  L'avanzamento è misurato sulla mappa: caselle scavate (prima metà della barra) ed edifici completati (seconda metà). I blueprint avviati sono registrati in `data/dfhack_blueprints.json` per fortezza; una zona ospita un solo blueprint.
- Misure minime delle zone: camere 11×9, laboratori 11×7, sala da pranzo 9×7, magazzini 10×6.
- Non ancora supportati su DFHack (l'autopilota li salta): espansione dei magazzini.
- Letti: scorte (oggetti utilizzabili: no proibiti, marci, del mercante, già costruiti), barre per materiale, oggetti a terra fuori dai magazzini, ordini di lavoro (anche `CustomReaction` per la birra), magazzini (caselle occupate/totali, categoria, link), laboratori e fornaci costruiti, cittadini (professione, stress, lavoro attuale, mansioni, abilità), minacce visibili (invasori, grandi pericoli, mercanti).
- **Zone per i blueprint**: le zone attività il cui nome inizia con `DDP` (es. `DDP Z1 Camere` → zona `Z1` "Camere"). Le altre zone sono ignorate.
- Ogni sezione dell'exporter è isolata: se una fallisce (es. campo rinominato in una versione di DFHack) compare in `errors` e il resto viene letto comunque.
- Lettura verificata su DFHack 53.17-r1 / DF 0.53.17 (Steam). Azioni verificate contro un ambiente DFHack simulato.

## Sicurezza del design

- L'autopilota esegue **solo** la dottrina approvata; ogni approvazione crea `data/history/doctrine_vNNNN.json`.
- Le patch di Gemini non possono toccare switch dei moduli, limiti o budget.
- Azioni idempotenti (tiene conto degli ordini in corso), massimo N azioni per ciclo (la pausa di sicurezza è sempre consentita).
- Cooldown per tipo di allarme: un allarme persistente mette in pausa una volta per finestra.
- Budget Free Tier lato client: richieste/minuto, richieste/giorno, cooldown per le analisi.

## Stato

- DFHack: lettura (2A) e azioni (2B) verificate in gioco; Quickfort (2C) implementato, da verificare in gioco.
- Il mock è un'approssimazione per sviluppare e dimostrare il flusso, non un modello fedele dell'economia di DF.
