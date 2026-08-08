"""Action list: the result of a plan or push run.

One action describes exactly one decision at exactly one place (a resource
path). `plan` only collects them, `push` carries them out — both use the same
walker (core/engine.py), so the dry run can never diverge from the real thing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Kinds of action. The first five write, the rest only report.
CREATE = "create"
UPDATE = "update"
DELETE = "delete"
UPLOAD = "upload"
ORDER = "order"
NOOP = "ok"
SKIP = "skip"
BLOCKED = "blocked"
DRIFT = "drift"
CONFLICT = "conflict"
OVERHANG = "overhang"
ERROR = "error"

WRITING = {CREATE, UPDATE, DELETE, UPLOAD, ORDER}
PROBLEMS = {CONFLICT, ERROR}
ATTENTION = {DRIFT, CONFLICT, OVERHANG, BLOCKED, ERROR}


@dataclass
class Action:
    kind: str
    path: str                     # e.g. "gameCenterAchievements/…first.win/localizations/en-US"
    detail: str = ""
    fields: Dict[str, Any] = field(default_factory=dict)
    executed: bool = False

    def line(self) -> str:
        fields = f" [{', '.join(sorted(self.fields))}]" if self.fields else ""
        detail = f" — {self.detail}" if self.detail else ""
        mark = "" if self.executed or self.kind not in WRITING else " (dry run)"
        return f"{self.kind:9} {self.path}{fields}{detail}{mark}"


@dataclass
class Plan:
    domain: str
    actions: List[Action] = field(default_factory=list)

    def add(self, kind: str, path: str, detail: str = "",
            fields: Optional[dict] = None, executed: bool = False) -> Action:
        action = Action(kind=kind, path=path, detail=detail,
                        fields=fields or {}, executed=executed)
        self.actions.append(action)
        return action

    def of_kind(self, *kinds: str) -> List[Action]:
        return [a for a in self.actions if a.kind in kinds]

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for a in self.actions:
            out[a.kind] = out.get(a.kind, 0) + 1
        return out

    def has_problems(self) -> bool:
        return any(a.kind in PROBLEMS for a in self.actions)

    def needs_attention(self) -> bool:
        return any(a.kind in ATTENTION for a in self.actions)

    def would_write(self) -> bool:
        return any(a.kind in WRITING for a in self.actions)
