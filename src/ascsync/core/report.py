"""Reporting: console output plus a machine-readable JSON report.

Exit codes (for CI):
  0  all good
  1  error (validation, API, abort)
  2  drift/conflict/overhang found — nothing is broken, but somebody worked in
     ASC by hand
"""
from __future__ import annotations

import json
import os
import time
from typing import List, Optional

from . import paths, planner

OK, PROBLEM, ATTENTION = 0, 1, 2


class Report:
    def __init__(self, command: str, dry_run: bool):
        self.command = command
        self.dry_run = dry_run
        self.started = time.time()
        self.plans: List[planner.Plan] = []
        self.messages: List[str] = []
        self.failed = False

    # -- collecting --------------------------------------------------------
    def plan_for(self, domain: str) -> planner.Plan:
        plan = planner.Plan(domain=domain)
        self.plans.append(plan)
        return plan

    def note(self, text: str) -> None:
        self.messages.append(text)
        print(text)

    def fail(self, text: str) -> None:
        self.failed = True
        self.note(f"[error] {text}")

    # -- printing ----------------------------------------------------------
    def summary(self) -> None:
        print("")
        print("=" * 72)
        title = f"{self.command}{' [dry run]' if self.dry_run else ''}"
        print(f"Summary — {title}")
        for plan in self.plans:
            counts = plan.counts()
            if not counts:
                print(f"  {plan.domain:12} —")
                continue
            parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
            print(f"  {plan.domain:12} {parts}")
        attention = [a for plan in self.plans for a in plan.actions
                     if a.kind in planner.ATTENTION]
        if attention:
            print("")
            print("Needs attention:")
            for a in attention:
                print(f"  - {a.line()}")
        if self.dry_run and any(p.would_write() for p in self.plans):
            print("")
            print("  Dry run — nothing written. Run again with --yes.")
        print(f"Took: {time.time() - self.started:.1f}s")
        print("=" * 72)

    def write_json(self, path: Optional[str] = None) -> str:
        path = path or os.path.join(paths.PROJECT_ROOT, ".report.json")
        payload = {
            "command": self.command,
            "dryRun": self.dry_run,
            "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "failed": self.failed,
            "messages": self.messages,
            "domains": [
                {
                    "domain": plan.domain,
                    "counts": plan.counts(),
                    "actions": [
                        {"kind": a.kind, "path": a.path, "detail": a.detail,
                         "fields": sorted(a.fields), "executed": a.executed}
                        for a in plan.actions
                    ],
                }
                for plan in self.plans
            ],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        return path

    def exit_code(self) -> int:
        if self.failed or any(p.has_problems() for p in self.plans):
            return PROBLEM
        if any(p.needs_attention() for p in self.plans):
            return ATTENTION
        return OK
