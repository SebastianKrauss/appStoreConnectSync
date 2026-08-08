"""Occurrence -> in-app event draft.

The recurrence fields of a recurring leaderboard (`recurrenceStartDate` +
`recurrenceDuration` + `recurrenceRule`) are expanded into the upcoming
occurrences, and each one becomes an event draft in data/events/events.json —
idempotent through the reference name `challenge-<metric>-<YYYY-MM-DD>`.

What the generator deliberately does NOT do:
  - invent images (it reports missing variants instead)
  - name the leaderboard, in either description
  - submit anything (review stays manual)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from ..core import domains, paths
from ..resources import events as events_res

TEMPLATES_FILE = "events/templates.json"
LIMITS = {"name": 30, "shortDescription": 50, "longDescription": 120}


# ---------------------------------------------------------------------------
# ISO-8601 duration and RRULE — without a hard dependency
# ---------------------------------------------------------------------------
_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?)?$")


def parse_duration(value: str) -> timedelta:
    match = _DURATION.match((value or "").strip().upper())
    if not match:
        raise SystemExit(f"Unverstandene Dauer '{value}' (erwartet z. B. P7D, PT15M).")
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return timedelta(days=parts["days"], hours=parts["hours"],
                     minutes=parts["minutes"], seconds=parts["seconds"])


def parse_datetime(value: str) -> datetime:
    text = (value or "").strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise SystemExit(f"Unverstandenes Datum '{value}'.")


def expand_rrule(start: datetime, rule: str, until: datetime) -> List[datetime]:
    """Occurrences up to `until`. Supports FREQ=DAILY|WEEKLY|MONTHLY with
    INTERVAL and COUNT — enough for recurring leaderboards. Anything more
    exotic is handled by python-dateutil, if it is installed."""
    if not rule:
        return [start]
    try:
        from dateutil.rrule import rrulestr        # optional
        return list(rrulestr(rule, dtstart=start).between(
            start - timedelta(seconds=1), until, inc=True))
    except ImportError:
        pass
    parts = dict(p.split("=", 1) for p in rule.upper().split(";") if "=" in p)
    freq = parts.get("FREQ", "DAILY")
    interval = int(parts.get("INTERVAL", "1"))
    count = int(parts["COUNT"]) if "COUNT" in parts else None
    step = {"DAILY": timedelta(days=interval),
            "WEEKLY": timedelta(weeks=interval),
            "MONTHLY": timedelta(days=30 * interval)}.get(freq)
    if step is None:
        raise SystemExit(f"FREQ={freq} braucht python-dateutil "
                         f"(pip install python-dateutil).")
    out, current = [], start
    while current <= until and (count is None or len(out) < count):
        out.append(current)
        current = current + step
    return out


# ---------------------------------------------------------------------------
# Spielart je Termin
# ---------------------------------------------------------------------------
def play_mode(start: datetime) -> str:
    """Which play mode falls on this date?

    EXAMPLE RULE: one-shot beats raw — every ISO week divisible by 5 is
    oneShot, otherwise an even week is raw and an odd week is normal, based on
    the ISO week of the start date in UTC.

    This function has to mirror the same rule as the app, otherwise the event
    advertises something other than what is played. If you have no play modes,
    always return "normal"."""
    week = start.astimezone(timezone.utc).isocalendar()[1]
    if week % 5 == 0:
        return "oneShot"
    return "raw" if week % 2 == 0 else "normal"


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------
def _primary_locale() -> str:
    """Primary language: from app.json, otherwise the first in locales.json."""
    configured = (paths.load_app_config().get("primaryLocale") or "").strip()
    if configured:
        return configured
    locales = paths.load_locales()
    return locales[0] if locales else "en-US"


def load_templates() -> dict:
    return paths.read_json(paths.data_path(TEMPLATES_FILE))


def generate(ahead_days: int = 84, now: Optional[datetime] = None,
             territories: Optional[List[str]] = None) -> Tuple[List[dict], List[str]]:
    """Returns (list of events, messages). Writes nothing."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(days=ahead_days)
    templates = load_templates()
    defaults = templates.get("defaults", {})
    messages: List[str] = []

    from ..resources.game_center import LEADERBOARDS
    boards_doc = domains.load_doc(_boards_domain())
    boards = {str(b.get("vendorIdentifier")): b
              for b in domains.doc_items(boards_doc, LEADERBOARDS)}

    existing = {str(e.get("referenceName")): e for e in _existing_events()}
    generated: List[dict] = []

    for metric, board_id in (templates.get("leaderboards") or {}).items():
        board = boards.get(board_id)
        if not board:
            messages.append(f"[warn] {metric}: leaderboard '{board_id}' is not in "
                            f"data/gamecenter/leaderboards.json — erst "
                            f"'ascsync pull --domain gamecenter' first.")
            continue
        if not board.get("recurrenceStartDate"):
            messages.append(f"[warn] {metric}: '{board_id}' has no "
                            f"recurrenceStartDate — no occurrences to derive.")
            continue
        start = parse_datetime(board["recurrenceStartDate"])
        duration = parse_duration(board.get("recurrenceDuration") or "P7D")
        occurrences = expand_rrule(start, board.get("recurrenceRule") or "", horizon)
        for occurrence in occurrences:
            if occurrence + duration < now:
                continue                      # bereits vorbei
            reference = f"challenge-{metric}-{occurrence.strftime('%Y-%m-%d')}"
            if reference in existing:
                generated.append(existing[reference])
                continue
            event, notes = _build_event(reference, metric, occurrence, duration,
                                        templates, defaults, territories)
            messages.extend(notes)
            generated.append(event)

    generated.sort(key=lambda e: e.get("referenceName", ""))
    messages.extend(_check_quota(generated, now))
    return generated, messages


def _boards_domain():
    from ..resources.game_center import LEADERBOARDS_DOMAIN
    return LEADERBOARDS_DOMAIN


def _existing_events() -> List[dict]:
    doc = domains.load_doc(events_res.EVENTS_DOMAIN)
    return domains.doc_items(doc, events_res.EVENTS) if doc else []


def _build_event(reference: str, metric: str, start: datetime, duration: timedelta,
                 templates: dict, defaults: dict,
                 territories: Optional[List[str]]) -> Tuple[dict, List[str]]:
    messages: List[str] = []
    mode = play_mode(start)
    end = start + duration
    lead = min(int(defaults.get("publishLeadDays", 7)),
               events_res.MAX_PUBLISH_LEAD_DAYS)
    publish = start - timedelta(days=lead)
    if duration.days > events_res.MAX_DURATION_DAYS:
        messages.append(f"[warn] {reference}: Dauer {duration.days} Tage > "
                        f"{events_res.MAX_DURATION_DAYS} — ASC will refuse it.")

    schedule = {
        "publishStart": _iso(publish),
        "eventStart": _iso(start),
        "eventEnd": _iso(end),
    }
    # Territories are derived, not listed: everywhere the app is available
    # (data/pricing.json, which comes from the pull) minus the exclusion list.
    # That keeps the answer right when availability changes.
    exclude = {t.upper() for t in (defaults.get("territoriesExclude") or [])}
    available = territories if territories is not None else (defaults.get("territories") or [])
    used = [t for t in available if t.upper() not in exclude]
    if used:
        schedule["territories"] = used
        if exclude:
            messages.append(f"[info] {reference}: {len(used)} Territorien "
                            f"(ausgeschlossen: {', '.join(sorted(exclude))}).")
    else:
        messages.append(f"[warn] {reference}: no territories — 'ascsync pull "
                        f"--domain pricing' fetches availability, or fill in "
                        f"defaults.territories in templates.json by hand.")

    event = {
        "referenceName": reference,
        "badge": defaults.get("badge", "COMPETITION"),
        # No default: a dead deep link is a common reason for rejection, and a
        # guessed one would be a guaranteed reason.
        "deepLink": (defaults.get("deepLink") or "").format(metric=metric),
        "purchaseRequirement": defaults.get("purchaseRequirement",
                                            "NO_COST_ASSOCIATED"),
        "primaryLocale": defaults.get("primaryLocale") or _primary_locale(),
        "priority": defaults.get("priority", "HIGH"),
        "purpose": defaults.get("purpose", "KEEP_ACTIVE_USERS_INFORMED"),
        "territorySchedules": [schedule],
        # Extra local fields: they steer asset resolution and never reach ASC.
        "metric": metric,
        "playMode": mode,
        "assetVariant": f"{metric}-{mode}",
        "localizations": {},
    }

    for locale, texts in (templates.get("texts") or {}).items():
        rendered, notes = _render(texts, locale, metric, mode, start, end, reference)
        event["localizations"][locale] = rendered
        messages.extend(notes)
    return event, messages


def _render(texts: dict, locale: str, metric: str, mode: str, start: datetime,
            end: datetime, reference: str) -> Tuple[dict, List[str]]:
    messages: List[str] = []
    values = {
        "metric": metric,
        "metricName": (texts.get("metricNames") or {}).get(metric, metric),
        "playMode": mode,
        "playModeName": (texts.get("playModeNames") or {}).get(mode, mode),
        "reward": (texts.get("rewards") or {}).get(metric, ""),
        "start": start.strftime("%d.%m."),
        "end": end.strftime("%d.%m."),
    }
    overrides = (texts.get("overrides") or {}).get(mode, {})
    out = {}
    for field in ("name", "shortDescription", "longDescription"):
        template = _pick(overrides.get(field, texts.get(field, "")), metric)
        try:
            value = template.format(**values)
        except KeyError as e:
            messages.append(f"[warn] {reference}/{locale}/{field}: unbekannter "
                            f"Platzhalter {e}")
            value = template
        if len(value) > LIMITS[field]:
            messages.append(f"[warn] {reference}/{locale}/{field}: {len(value)} characters "
                            f"(limit {LIMITS[field]}) — shorten the template")
        out[field] = value
    return out, messages


def _pick(template, metric: str) -> str:
    """Templates may differ per metric: either one string for all of them, or a
    dict {metric: text} (with an optional 'default')."""
    if isinstance(template, dict):
        return template.get(metric, template.get("default", ""))
    return template or ""


def _check_quota(events: List[dict], now: datetime) -> List[str]:
    """Quota guard: better to stop here than build drafts ASC will reject."""
    messages: List[str] = []
    if len(events) > events_res.MAX_APPROVED:
        messages.append(f"[stopp] {len(events)} Events — ASC nimmt hoechstens "
                        f"{events_res.MAX_APPROVED} approved. Shorten the lead "
                        f"--ahead kuerzen.")
    windows = []
    for event in events:
        for schedule in event.get("territorySchedules") or []:
            start = parse_datetime(schedule.get("eventStart", ""))
            end = parse_datetime(schedule.get("eventEnd", ""))
            windows.append((start, end))
    overlapping = 0
    for i, (start_a, end_a) in enumerate(windows):
        count = sum(1 for j, (start_b, end_b) in enumerate(windows)
                    if j != i and start_b < end_a and start_a < end_b)
        overlapping = max(overlapping, count + 1)
    if overlapping > events_res.MAX_OVERLAPPING:
        messages.append(f"[stopp] bis zu {overlapping} gleichzeitig laufende Events — "
                        f"ASC erlaubt {events_res.MAX_OVERLAPPING}.")
    published = sum(1 for start, end in windows
                    if start - timedelta(days=events_res.MAX_PUBLISH_LEAD_DAYS) <= now < end)
    if published > events_res.MAX_PUBLISHED:
        messages.append(f"[stopp] {published} gleichzeitig veroeffentlichte Events — "
                        f"ASC erlaubt {events_res.MAX_PUBLISHED}.")
    return messages


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(events: List[dict]) -> str:
    doc = domains.pack_doc(events_res.EVENTS, events, strip_ids=True)
    return domains.save_doc(events_res.EVENTS_DOMAIN, doc)
