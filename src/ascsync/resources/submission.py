"""Submission (`reviewSubmissions`) — the modern way to send an app version,
an event or a product page for review.

The sequence: find or create an open submission -> attach the item as a
`reviewSubmissionItem` -> send the submission with `submitted=true`. That last
step is deliberately the only one with an outward effect; without `--yes`
everything stays a dry run.
"""
from __future__ import annotations

from typing import Optional

from ..core import domains, planner

PLATFORM = "IOS"


def open_submission(ctx: domains.Context) -> Optional[dict]:
    for submission in ctx.client.get_all(f"/v1/apps/{ctx.app_id}/reviewSubmissions",
                                         **{"filter[platform]": PLATFORM}):
        state = (submission.get("attributes", {}) or {}).get("state", "")
        if state in ("READY_FOR_REVIEW", "WAITING_FOR_REVIEW", "IN_REVIEW", "UNRESOLVED_ISSUES"):
            return submission
    return None


def ensure_submission(ctx: domains.Context, plan: planner.Plan) -> Optional[str]:
    existing = open_submission(ctx)
    if existing:
        plan.add(planner.NOOP, "reviewSubmission",
                 f"offene Einreichung {existing['id']} "
                 f"({(existing.get('attributes') or {}).get('state')})")
        return existing["id"]
    plan.add(planner.CREATE, "reviewSubmission", "neue Einreichung",
             executed=not ctx.client.dry_run)
    created = ctx.client.post("/v1/reviewSubmissions", {
        "data": {
            "type": "reviewSubmissions",
            "attributes": {"platform": PLATFORM},
            "relationships": {"app": {"data": {"type": "apps", "id": ctx.app_id}}},
        }
    })
    return created["data"]["id"] if created else None


def add_item(ctx: domains.Context, plan: planner.Plan, submission_id: str,
             relationship: str, item_type: str, item_id: str, label: str) -> None:
    if not submission_id:
        return
    for item in ctx.client.get_all(f"/v1/reviewSubmissions/{submission_id}/items"):
        related = (item.get("relationships", {}).get(relationship, {}).get("data") or {})
        if related.get("id") == item_id:
            plan.add(planner.NOOP, f"reviewSubmissionItem/{label}", "bereits enthalten")
            return
    plan.add(planner.CREATE, f"reviewSubmissionItem/{label}",
             executed=not ctx.client.dry_run)
    ctx.client.post("/v1/reviewSubmissionItems", {
        "data": {
            "type": "reviewSubmissionItems",
            "relationships": {
                "reviewSubmission": {"data": {"type": "reviewSubmissions",
                                              "id": submission_id}},
                relationship: {"data": {"type": item_type, "id": item_id}},
            },
        }
    })


def submit(ctx: domains.Context, plan: planner.Plan, submission_id: str) -> None:
    if not submission_id:
        return
    plan.add(planner.UPDATE, "reviewSubmission/submitted", "send for review",
             fields={"submitted": True}, executed=not ctx.client.dry_run)
    ctx.client.patch(f"/v1/reviewSubmissions/{submission_id}", {
        "data": {"type": "reviewSubmissions", "id": submission_id,
                 "attributes": {"submitted": True}}
    })
