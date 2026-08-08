"""When does each event publish, run and need to be through review?

Three dates per event and one of them is implicit. That is enough arithmetic to
get wrong, and getting it wrong is how a launch date slips: an event whose
publication falls before the app is live, or whose review window turns out to
be four days after somebody promised a date.

    ascsync events calendar
    ascsync events calendar --weeks 26

Deadlines are shown, not enforced. Apple publishes no fixed review time for
in-app events; a week of slack is the number this project settled on after
watching real submissions, and it is a habit, not a rule. Treat a red line as
"you are relying on luck", not as "this will fail".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from ..core import domains, paths
from ..resources import events as events_res

# What a comfortable submission looks like: through review a week before the
# event card appears on the product page.
REVIEW_SLACK_DAYS = 7


def _at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def rows(items: List[dict], now: Optional[datetime] = None,
         weeks: int = 12) -> List[dict]:
    """One row per event, sorted by start; past events are left out."""
    now = now or datetime.now(timezone.utc)
    horizon = now + timedelta(weeks=weeks)
    out = []
    for item in items:
        for schedule in item.get("territorySchedules") or []:
            start = _at(schedule.get("eventStart"))
            end = _at(schedule.get("eventEnd"))
            publish = _at(schedule.get("publishStart"))
            if not start or start > horizon:
                continue
            if end and end < now:
                continue
            submit_by = publish - timedelta(days=REVIEW_SLACK_DAYS) if publish else None
            out.append({
                "reference": str(item.get("referenceName", "?")),
                "state": ((item.get("readonly") or {}).get("eventState")
                          or "DRAFT"),
                "submitBy": submit_by,
                "publish": publish,
                "start": start,
                "end": end,
                "late": bool(submit_by and submit_by < now),
                "days": (start - now).days,
            })
    return sorted(out, key=lambda r: r["start"])


def overlaps(entries: List[dict]) -> List[Tuple[str, str]]:
    """Pairs whose run time overlaps — usually a mistake, occasionally not."""
    clashes = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            if a["end"] and b["start"] and a["start"] < b["end"] and b["start"] < a["end"]:
                clashes.append((a["reference"], b["reference"]))
    return clashes


def render(weeks: int = 12, now: Optional[datetime] = None) -> List[str]:
    doc = domains.load_doc(events_res.EVENTS_DOMAIN)
    items = domains.doc_items(doc, events_res.EVENTS) if doc else []
    entries = rows(items, now=now, weeks=weeks)
    if not entries:
        return [f"No events starting within {weeks} weeks. "
                f"'ascsync events generate' makes drafts from the recurrence "
                f"of your leaderboards."]

    def day(value: Optional[datetime]) -> str:
        return value.strftime("%a %d %b") if value else "—"

    lines = [f"{'submit by':>13}  {'publishes':>13}  {'runs':>13} → {'ends':>13}"
             f"   in   state      event", ""]
    for row in entries:
        mark = "!" if row["late"] else " "
        lines.append(f"{mark}{day(row['submitBy']):>12}  {day(row['publish']):>13}  "
                     f"{day(row['start']):>13} → {day(row['end']):>13}  "
                     f"{row['days']:>3}d  {row['state']:<10} {row['reference']}")

    late = [r for r in entries if r["late"]]
    if late:
        lines += ["", f"! {len(late)} event(s) should already be in review to keep "
                      f"a week of slack before they publish.",
                  "  Not a hard failure — Apple publishes no fixed review time — "
                  "but you are relying on luck."]
    clashes = overlaps(entries)
    if clashes:
        lines += [""] + [f"~ {a} and {b} run at the same time" for a, b in clashes]

    published = sum(1 for r in entries if r["state"] == "PUBLISHED")
    if published > events_res.MAX_PUBLISHED:
        lines += ["", f"! {published} published at once; Apple allows "
                      f"{events_res.MAX_PUBLISHED}."]
    return lines
