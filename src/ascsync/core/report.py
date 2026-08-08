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

    def append_write_log(self) -> Optional[str]:
        """One line per thing actually written, appended for ever.

        `.requests.log` records HTTP traffic, which answers "what did the tool
        send". This answers the question you have three weeks later: who
        changed the German description, and when. Plain text, one line each, so
        `grep` is enough.
        """
        if self.dry_run:
            return None
        written = [a for plan in self.plans for a in plan.actions
                   if a.kind in planner.WRITING and a.executed]
        if not written:
            return None
        path = os.path.join(paths.PROJECT_ROOT, ".writes.log")
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            for action in written:
                fields = f" [{', '.join(sorted(action.fields))}]" if action.fields else ""
                f.write(f"{stamp}\t{self.command}\t{action.kind}\t"
                        f"{action.path}{fields}\n")
        return path

    # -- the dry-run receipt -----------------------------------------------
    #
    # A dry run leaves a fingerprint of what it saw. `push --yes --require-dry-run`
    # refuses unless that fingerprint matches what it is about to do — so
    # "somebody looked at this first" becomes a mechanism rather than a habit.
    # It is not a security boundary: --require-dry-run is opt-in, and anyone
    # can leave it off. It is there to stop an agent, or a tired human, from
    # skipping the one step that makes the rest safe.
    def fingerprint(self) -> str:
        import hashlib
        material = "|".join(f"{a.kind}:{a.path}:{','.join(sorted(a.fields))}"
                            for plan in self.plans for a in plan.actions
                            if a.kind in planner.WRITING)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def save_receipt(self) -> None:
        paths.write_json(os.path.join(paths.PROJECT_ROOT, ".dryrun.json"),
                         {"command": self.command,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                          "fingerprint": self.fingerprint()})

    def matching_receipt(self) -> bool:
        path = os.path.join(paths.PROJECT_ROOT, ".dryrun.json")
        if not os.path.exists(path):
            return False
        try:
            return json.load(open(path, encoding="utf-8")).get("fingerprint") == self.fingerprint()
        except (ValueError, OSError):
            return False

    def exit_code(self) -> int:
        if self.failed or any(p.has_problems() for p in self.plans):
            return PROBLEM
        if any(p.needs_attention() for p in self.plans):
            return ATTENTION
        return OK
