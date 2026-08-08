"""A plan as a page you can actually read.

`plan` prints a list. That is fine for eight actions and useless for four
hundred, which is what a first push looks like: ninety text fields and
forty-eight screenshots, and somewhere in there the one line that matters.

This renders the same plan as HTML — grouped by domain, sorted so the things
that need a decision come first, with the counts up top and every screenshot
shown rather than named. No JavaScript, no external files, no fonts to fetch:
one file you can open, attach to a CI run, or link from a pull request.

Images are embedded as data URIs. That makes the file large — a first push
lands around a megabyte — and it also makes it self-contained, which is the
whole point of something you hand to someone else.
"""
from __future__ import annotations

import base64
import html
import mimetypes
import os
from typing import List, Optional

from . import paths, planner

# Kinds that need a human decision, in the order a human should meet them.
_PRIORITY = [planner.CONFLICT, planner.ERROR, planner.DRIFT, planner.OVERHANG,
             planner.BLOCKED, planner.SKIP, planner.CREATE, planner.UPDATE,
             planner.DELETE, planner.UPLOAD, planner.ORDER, planner.NOOP]

_COLOURS = {
    planner.CONFLICT: "#b3261e", planner.ERROR: "#b3261e",
    planner.DRIFT: "#a15c00", planner.OVERHANG: "#a15c00",
    planner.BLOCKED: "#5f6368", planner.SKIP: "#5f6368",
    planner.CREATE: "#146c2e", planner.UPDATE: "#1a56c4",
    planner.DELETE: "#8a3f00", planner.UPLOAD: "#1a56c4",
    planner.ORDER: "#5f6368", planner.NOOP: "#5f6368",
}

_STYLE = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 0 auto; max-width: 60rem; padding: 2rem 1.25rem 4rem; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; }
h2 { font-size: 1.1rem; margin: 2.5rem 0 .5rem; padding-bottom: .3rem;
     border-bottom: 1px solid rgba(128,128,128,.35); }
.sub { color: #5f6368; margin: 0 0 1.5rem; }
.counts { display: flex; flex-wrap: wrap; gap: .4rem; margin: 0 0 1rem; padding: 0; }
.counts li { list-style: none; border: 1px solid rgba(128,128,128,.35);
             border-radius: 999px; padding: .1rem .6rem; font-size: .85rem; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
td { padding: .35rem .5rem; vertical-align: top;
     border-top: 1px solid rgba(128,128,128,.2); }
td.kind { white-space: nowrap; font-weight: 600; width: 6.5rem; }
td.path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
          word-break: break-all; }
td.detail { color: #5f6368; }
.fields { color: #1a56c4; font-family: ui-monospace, Menlo, monospace;
          font-size: .85rem; }
.shots { display: flex; flex-wrap: wrap; gap: .6rem; margin: .75rem 0 0; }
.shot { width: 150px; }
.shot img { width: 100%; border: 1px solid rgba(128,128,128,.35); border-radius: 4px;
            display: block; background: #fff; }
.shot span { display: block; font-size: .75rem; color: #5f6368;
             word-break: break-all; margin-top: .2rem; }
.empty { color: #5f6368; font-style: italic; }
"""


def _thumbnail(path: str, width: int = 300) -> Optional[str]:
    """A data URI for an image, downscaled when Pillow is around.

    Without Pillow the original is embedded. That is heavier but still
    correct — an optional dependency should never decide whether a report can
    be produced at all.
    """
    kind, _ = mimetypes.guess_type(path)
    if not kind or not kind.startswith("image/"):
        return None
    try:
        from io import BytesIO

        from PIL import Image                      # optional
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((width, width * 4))
            buffer = BytesIO()
            image.save(buffer, "JPEG", quality=72)
            raw, kind = buffer.getvalue(), "image/jpeg"
    except Exception:
        try:
            raw = open(path, "rb").read()
        except OSError:
            return None
    return f"data:{kind};base64," + base64.b64encode(raw).decode("ascii")


def _gallery(actions: List[planner.Action]) -> str:
    """Every image this plan would upload, as pictures rather than filenames."""
    tiles = []
    for action in actions:
        if action.kind != planner.UPLOAD or not action.detail:
            continue
        local = _find_asset(action.detail)
        if not local:
            continue
        uri = _thumbnail(local)
        if not uri:
            continue
        tiles.append(f'<div class="shot"><img src="{uri}" alt="">'
                     f'<span>{html.escape(action.path)}</span></div>')
    if not tiles:
        return ""
    return f'<div class="shots">{"".join(tiles)}</div>'


def _find_asset(filename: str) -> Optional[str]:
    """Locate an uploaded file by name under assets/ (first match wins)."""
    base = os.path.basename(filename.split(" ")[0])
    for root, _dirs, files in os.walk(paths.ASSETS_DIR):
        if base in files:
            return os.path.join(root, base)
    return None


def render(rep, title: str = "ascsync") -> str:
    parts = [f"<!doctype html><meta charset='utf-8'><title>{html.escape(title)}</title>",
             f"<style>{_STYLE}</style>",
             f"<h1>{html.escape(title)}</h1>"]
    mode = "dry run — nothing was written" if rep.dry_run else "written to App Store Connect"
    parts.append(f"<p class='sub'>{html.escape(rep.command)} · {mode}</p>")

    for plan in rep.plans:
        counts = plan.counts()
        parts.append(f"<h2>{html.escape(plan.domain)}</h2>")
        if not counts:
            parts.append("<p class='empty'>nothing to do</p>")
            continue
        chips = "".join(f"<li>{html.escape(k)}: {v}</li>"
                        for k, v in sorted(counts.items()))
        parts.append(f"<ul class='counts'>{chips}</ul>")

        rows = []
        order = {kind: i for i, kind in enumerate(_PRIORITY)}
        for action in sorted(plan.actions, key=lambda a: order.get(a.kind, 99)):
            if action.kind == planner.NOOP:
                continue
            colour = _COLOURS.get(action.kind, "#5f6368")
            fields = (f"<span class='fields'>{html.escape(', '.join(sorted(action.fields)))}</span>"
                      if action.fields else "")
            rows.append(
                f"<tr><td class='kind' style='color:{colour}'>{html.escape(action.kind)}</td>"
                f"<td class='path'>{html.escape(action.path)} {fields}</td>"
                f"<td class='detail'>{html.escape(action.detail)}</td></tr>")
        if rows:
            parts.append(f"<table>{''.join(rows)}</table>")
        else:
            parts.append("<p class='empty'>everything already matches</p>")
        parts.append(_gallery(plan.actions))
    return "\n".join(parts)


def write(rep, path: str, title: str = "ascsync") -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(rep, title))
    return path
