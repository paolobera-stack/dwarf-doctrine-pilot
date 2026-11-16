"""Minimal dual localisation: Italian (default) and English.

Messages are stored as (key, params) wherever possible and rendered at display
time, so switching language re-renders logs and alerts too.
"""

from __future__ import annotations

from collections.abc import Callable

LANGUAGES = {"it": "Italiano", "en": "English"}

_STRINGS: dict[str, dict[str, str]] = {
    # --- window / toolbar
    "app.title": {"it": "Dwarf Doctrine Pilot", "en": "Dwarf Doctrine Pilot"},
    "toolbar.autopilot_on": {"it": "Autopilota: ATTIVO", "en": "Autopilot: ON"},
    "toolbar.autopilot_off": {"it": "Autopilota: SPENTO", "en": "Autopilot: OFF"},
    "toolbar.dry_run": {"it": "Simulazione (dry-run)", "en": "Dry run"},
    "toolbar.pause_game": {"it": "Pausa gioco", "en": "Pause game"},
    "toolbar.resume_game": {"it": "Riprendi gioco", "en": "Resume game"},
    "toolbar.language": {"it": "Lingua", "en": "Language"},
    "status.connected": {"it": "Connesso: {bridge}", "en": "Connected: {bridge}"},
    "status.disconnected": {"it": "Disconnesso: {error}", "en": "Disconnected: {error}"},
    "status.date": {
        "it": "Anno {year}, {season}, giorno {day} | Pop. {pop} | {paused}",
        "en": "Year {year}, {season}, day {day} | Pop. {pop} | {paused}",
    },
    "status.paused": {"it": "IN PAUSA", "en": "PAUSED"},
    "status.running": {"it": "in corso", "en": "running"},
    "season.spring": {"it": "Primavera", "en": "Spring"},
    "season.summer": {"it": "Estate", "en": "Summer"},
    "season.autumn": {"it": "Autunno", "en": "Autumn"},
    "season.winter": {"it": "Inverno", "en": "Winter"},
    "filter.placeholder": {"it": "Filtra…", "en": "Filter…"},
    # --- tabs
    "tab.production": {"it": "Produzione & Work Orders", "en": "Production & Work Orders"},
    "tab.stockpiles": {"it": "Magazzini & Logistica", "en": "Stockpiles & Logistics"},
    "tab.blueprints": {"it": "Pianificazione & Blueprints", "en": "Planning & Blueprints"},
    "tab.dwarves": {"it": "Nani & Ruoli", "en": "Dwarves & Roles"},
    "section.stocks": {"it": "Scorte vs target dottrina", "en": "Stocks vs doctrine targets"},
    "section.orders": {"it": "Work Orders", "en": "Work Orders"},
    "section.stockpiles": {"it": "Magazzini", "en": "Stockpiles"},
    "section.zones": {"it": "Zone designate", "en": "Designated zones"},
    "section.queue": {"it": "Coda blueprint (dottrina + stato)", "en": "Blueprint queue (doctrine + state)"},
    "logistics.summary": {
        "it": "Oggetti a terra: {ground}  |  Riempimento medio: {avg}%  |  Link attivi: {links}",
        "en": "Items on ground: {ground}  |  Average fill: {avg}%  |  Active links: {links}",
    },
    # --- columns
    "col.item": {"it": "Oggetto", "en": "Item"},
    "col.current": {"it": "Attuale", "en": "Current"},
    "col.min": {"it": "Min", "en": "Min"},
    "col.target": {"it": "Target", "en": "Target"},
    "col.max": {"it": "Max", "en": "Max"},
    "col.on_order": {"it": "In ordine", "en": "On order"},
    "col.coverage": {"it": "Copertura", "en": "Coverage"},
    "col.status": {"it": "Stato", "en": "Status"},
    "col.id": {"it": "ID", "en": "ID"},
    "col.job": {"it": "Lavoro", "en": "Job"},
    "col.quantity": {"it": "Quantità", "en": "Quantity"},
    "col.remaining": {"it": "Rimanenti", "en": "Remaining"},
    "col.source": {"it": "Origine", "en": "Source"},
    "col.name": {"it": "Nome", "en": "Name"},
    "col.category": {"it": "Categoria", "en": "Category"},
    "col.used": {"it": "Usato/Capacità", "en": "Used/Capacity"},
    "col.fill": {"it": "Riempimento", "en": "Fill"},
    "col.ground": {"it": "A terra", "en": "On ground"},
    "col.links": {"it": "Link", "en": "Links"},
    "col.rule": {"it": "Regola dottrina", "en": "Doctrine rule"},
    "col.queue_id": {"it": "Coda", "en": "Queue"},
    "col.blueprint": {"it": "Blueprint", "en": "Blueprint"},
    "col.zone": {"it": "Zona", "en": "Zone"},
    "col.priority": {"it": "Priorità", "en": "Priority"},
    "col.progress": {"it": "Avanzamento", "en": "Progress"},
    "col.preconditions": {"it": "Precondizioni", "en": "Preconditions"},
    "col.size": {"it": "Dimensioni", "en": "Size"},
    "col.assigned": {"it": "Assegnata a", "en": "Assigned to"},
    "col.profession": {"it": "Professione", "en": "Profession"},
    "col.stress": {"it": "Stress", "en": "Stress"},
    "col.activity": {"it": "Attività", "en": "Activity"},
    # --- statuses
    "stock.ok": {"it": "OK", "en": "OK"},
    "stock.low": {"it": "Sotto min", "en": "Below min"},
    "stock.critical": {"it": "Esaurito", "en": "Depleted"},
    "stock.excess": {"it": "Eccesso", "en": "Excess"},
    "stock.untracked": {"it": "Non in dottrina", "en": "Not in doctrine"},
    "order.queued": {"it": "In coda", "en": "Queued"},
    "order.active": {"it": "Attivo", "en": "Active"},
    "order.done": {"it": "Completato", "en": "Done"},
    "order.cancelled": {"it": "Annullato", "en": "Cancelled"},
    "source.autopilot": {"it": "Autopilota", "en": "Autopilot"},
    "source.user": {"it": "Utente", "en": "User"},
    "source.emergency": {"it": "Emergenza", "en": "Emergency"},
    "bp.planned": {"it": "Pianificato", "en": "Planned"},
    "bp.blocked": {"it": "Bloccato", "en": "Blocked"},
    "bp.queued": {"it": "In coda", "en": "Queued"},
    "bp.digging": {"it": "Scavo", "en": "Digging"},
    "bp.building": {"it": "Costruzione", "en": "Building"},
    "bp.done": {"it": "Completato", "en": "Completed"},
    "bp.missing_zone": {"it": "Zona mancante", "en": "Missing zone"},
    "bp.free": {"it": "Libera", "en": "Free"},
    "pre.none": {"it": "nessuna", "en": "none"},
    "pre.population": {"it": "pop ≥ {n}", "en": "pop ≥ {n}"},
    "pre.after": {"it": "dopo {ids}", "en": "after {ids}"},
    "rule.none": {"it": "—", "en": "—"},
    "rule.summary": {"it": "max {pct}%", "en": "max {pct}%"},
    "rule.expand": {"it": "+{n} celle", "en": "+{n} cells"},
    "rule.container": {"it": "contenitori: {job}", "en": "containers: {job}"},
    # --- doctrine panel
    "panel.title": {"it": "Dottrina & Chat", "en": "Doctrine & Chat"},
    "panel.directives": {"it": "Direttive attive — v{version}: {name}", "en": "Active directives — v{version}: {name}"},
    "panel.modules": {"it": "Moduli di automazione", "en": "Automation modules"},
    "module.production": {"it": "Produzione", "en": "Production"},
    "module.stockpiles": {"it": "Magazzini", "en": "Stockpiles"},
    "module.blueprints": {"it": "Blueprint", "en": "Blueprints"},
    "module.emergency": {"it": "Emergenza", "en": "Emergency"},
    "directive.stock": {"it": "{item}: min {min} / target {target} / max {max}", "en": "{item}: min {min} / target {target} / max {max}"},
    "directive.stockpile": {"it": "Magazzino {category}: max {pct}%", "en": "Stockpile {category}: max {pct}%"},
    "directive.blueprint": {"it": "Blueprint {id} → {zone} (prio {priority})", "en": "Blueprint {id} → {zone} (prio {priority})"},
    "directive.alarms": {
        "it": "Allarmi: cibo < {food}g, bevande < {drink}g, stress > {stress}",
        "en": "Alarms: food < {food}d, drink < {drink}d, stress > {stress}",
    },
    "directive.group.stocks": {"it": "Target scorte", "en": "Stock targets"},
    "directive.group.stockpiles": {"it": "Regole magazzini", "en": "Stockpile rules"},
    "directive.group.blueprints": {"it": "Coda espansione", "en": "Expansion queue"},
    "directive.group.alarms": {"it": "Soglie di allarme", "en": "Alarm thresholds"},
    "chat.title": {"it": "Chat strategica con Gemini", "en": "Strategy chat with Gemini"},
    "chat.placeholder": {"it": "Discuti la strategia della fortezza…", "en": "Discuss the fortress strategy…"},
    "chat.send": {"it": "Invia", "en": "Send"},
    "chat.you": {"it": "Tu", "en": "You"},
    "chat.thinking": {"it": "Gemini sta pensando…", "en": "Gemini is thinking…"},
    "chat.error": {"it": "Errore Gemini: {error}", "en": "Gemini error: {error}"},
    "chat.offline": {
        "it": "Modalità offline (nessuna GEMINI_API_KEY): risposte simulate.",
        "en": "Offline mode (no GEMINI_API_KEY): simulated replies.",
    },
    "chat.budget": {
        "it": "Payload {tokens}/{max} token · richieste oggi {day}/{day_max}",
        "en": "Payload {tokens}/{max} tokens · requests today {day}/{day_max}",
    },
    "chat.rate_limited": {"it": "Limite Free Tier: riprova tra {s}s.", "en": "Free Tier limit: retry in {s}s."},
    "proposal.title": {"it": "Proposta di aggiornamento dottrina", "en": "Doctrine update proposal"},
    "proposal.none": {"it": "Nessuna proposta in attesa.", "en": "No pending proposal."},
    "proposal.empty": {"it": "La proposta non cambia nulla.", "en": "The proposal changes nothing."},
    "proposal.invalid": {"it": "Proposta non valida: {error}", "en": "Invalid proposal: {error}"},
    "proposal.approve": {"it": "Approva e Automatizza", "en": "Approve & Automate"},
    "proposal.reject": {"it": "Rifiuta", "en": "Reject"},
    "proposal.approved": {"it": "Dottrina v{version} approvata e attiva.", "en": "Doctrine v{version} approved and active."},
    "proposal.rejected": {"it": "Proposta rifiutata.", "en": "Proposal rejected."},
    "diff.added": {"it": "aggiunto", "en": "added"},
    "diff.removed": {"it": "rimosso", "en": "removed"},
    "log.title": {"it": "Log autopilota (tempo reale)", "en": "Autopilot log (live)"},
    # --- autopilot actions
    "act.create_order": {
        "it": "Ordine {job} ×{quantity} ({item}: {current}+{pending} < min {min})",
        "en": "Order {job} ×{quantity} ({item}: {current}+{pending} < min {min})",
    },
    "act.cancel_order": {
        "it": "Annullato ordine #{order_id} {job} ({item} {current} > max {max})",
        "en": "Cancelled order #{order_id} {job} ({item} {current} > max {max})",
    },
    "act.container_order": {
        "it": "Contenitori {job} ×{quantity} per magazzino {name} ({fill}% > {max}%)",
        "en": "Containers {job} ×{quantity} for stockpile {name} ({fill}% > {max}%)",
    },
    "act.expand_stockpile": {
        "it": "Ampliato magazzino {name} di {cells} celle ({fill}% > {max}%)",
        "en": "Expanded stockpile {name} by {cells} cells ({fill}% > {max}%)",
    },
    "act.link_stockpile": {"it": "Collegato magazzino {name} → {workshop}", "en": "Linked stockpile {name} → {workshop}"},
    "act.enqueue_blueprint": {
        "it": "Accodato blueprint {blueprint} in zona {zone} ({id})",
        "en": "Queued blueprint {blueprint} in zone {zone} ({id})",
    },
    "act.emergency_order": {
        "it": "EMERGENZA: ordine {job} ×{quantity} ({item} copre {days:.1f} giorni)",
        "en": "EMERGENCY: order {job} ×{quantity} ({item} covers {days:.1f} days)",
    },
    "act.pause": {"it": "Gioco messo in PAUSA: {reason}", "en": "Game PAUSED: {reason}"},
    "act.failed": {"it": "FALLITO: {error}", "en": "FAILED: {error}"},
    "act.dry_run": {"it": "[dry-run] ", "en": "[dry-run] "},
    "act.limit": {
        "it": "Limite di {n} azioni per ciclo raggiunto, rinvio al prossimo ciclo",
        "en": "Limit of {n} actions per tick reached, deferring to next tick",
    },
    # --- alarms
    "alarm.food_low": {"it": "Cibo per {days:.1f} giorni (soglia {limit})", "en": "Food for {days:.1f} days (threshold {limit})"},
    "alarm.drink_low": {"it": "Bevande per {days:.1f} giorni (soglia {limit})", "en": "Drink for {days:.1f} days (threshold {limit})"},
    "alarm.stress": {
        "it": "{pct}% dei nani oltre lo stress critico",
        "en": "{pct}% of dwarves above critical stress",
    },
    "alarm.idle": {"it": "{pct}% dei nani inattivi", "en": "{pct}% of dwarves idle"},
    "alarm.ground": {"it": "{n} oggetti a terra", "en": "{n} items on the ground"},
    "alarm.event": {"it": "Evento: {message}", "en": "Event: {message}"},
    "alert.title": {"it": "Allarme critico", "en": "Critical alarm"},
    "alert.body": {
        "it": "{message}\n\nIl gioco è stato messo in pausa. Le regole di emergenza deterministiche sono state applicate dove possibile.",
        "en": "{message}\n\nThe game has been paused. Deterministic emergency rules were applied where possible.",
    },
    "alert.analyze": {"it": "Analizza con Gemini", "en": "Analyse with Gemini"},
    "alert.resume": {"it": "Riprendi gioco", "en": "Resume game"},
    "alert.dismiss": {"it": "Chiudi", "en": "Dismiss"},
    "alert.analysis_prompt": {
        "it": "ALLARME CRITICO: {message}. Analizza la situazione e proponi modifiche alla dottrina.",
        "en": "CRITICAL ALARM: {message}. Analyse the situation and propose doctrine changes.",
    },
    "toolbar.inject": {"it": "Simula evento (mock)", "en": "Inject event (mock)"},
    "toolbar.analyze": {"it": "Analisi strategica", "en": "Strategic analysis"},
    "event.siege": {"it": "Assedio", "en": "Siege"},
    "event.forgotten_beast": {"it": "Bestia dimenticata", "en": "Forgotten beast"},
    "event.cave_in": {"it": "Crollo", "en": "Cave-in"},
    "event.tantrum_spiral": {"it": "Spirale di capricci", "en": "Tantrum spiral"},
    "event.migrants": {"it": "Migranti", "en": "Migrants"},
    "event.caravan": {"it": "Carovana", "en": "Caravan"},
    "event.strange_mood": {"it": "Umore strano", "en": "Strange mood"},
    "log.started": {
        "it": "Autopilota avviato su {bridge}, dottrina v{version}, ciclo {s}s",
        "en": "Autopilot started on {bridge}, doctrine v{version}, tick {s}s",
    },
    "log.modules": {"it": "Moduli aggiornati: {modules}", "en": "Modules updated: {modules}"},
    "log.injected": {"it": "Evento simulato: {kind}", "en": "Injected event: {kind}"},
    "log.game_paused": {"it": "Gioco messo in pausa dall'utente", "en": "Game paused by user"},
    "log.game_resumed": {"it": "Gioco ripreso dall'utente", "en": "Game resumed by user"},
    "log.autopilot": {"it": "Autopilota {state}", "en": "Autopilot {state}"},
    "proposal.summary": {
        "it": "v{old} → v{new}: {n} modifiche",
        "en": "v{old} → v{new}: {n} changes",
    },
    "chat.analysis_prompt": {
        "it": "Fai un'analisi strategica della fortezza e proponi modifiche alla dottrina se servono.",
        "en": "Give a strategic analysis of the fortress and propose doctrine changes if needed.",
    },
    "level.warning": {"it": "ATTENZIONE", "en": "WARNING"},
    "level.critical": {"it": "CRITICO", "en": "CRITICAL"},
}

_lang = "it"
_listeners: list[Callable[[str], None]] = []


def set_language(lang: str) -> None:
    global _lang
    if lang not in LANGUAGES:
        raise ValueError(f"unsupported language: {lang}")
    if lang != _lang:
        _lang = lang
        for cb in list(_listeners):
            cb(lang)


def language() -> str:
    return _lang


def on_language_changed(cb: Callable[[str], None]) -> None:
    _listeners.append(cb)


def off_language_changed(cb: Callable[[str], None]) -> None:
    if cb in _listeners:
        _listeners.remove(cb)


def tr(key: str, **params: object) -> str:
    entry = _STRINGS.get(key)
    if entry is None:
        return key
    text = entry.get(_lang) or entry["it"]
    try:
        return text.format(**params) if params else text
    except (KeyError, IndexError, ValueError):
        return text


def has_key(key: str) -> bool:
    return key in _STRINGS


def all_keys() -> dict[str, dict[str, str]]:
    return _STRINGS
