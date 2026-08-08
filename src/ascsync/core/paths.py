"""Paths and JSON I/O.

One place for every directory: data/ (the editable truth), assets/ (images and
videos), .snapshot/ (the ASC state as of the last pull).
"""
from __future__ import annotations

import json
import os
from typing import Any

# Where does the content live?
#
# By default in the current working directory: data/, assets/, .snapshot/ and
# assets.lock.json all sit there. A project is therefore exactly one directory,
# and a single installed ascsync serves any number of apps. ASCSYNC_PROJECT
# names that directory explicitly, regardless of the working directory.
PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = os.path.abspath(os.path.expanduser(
    os.environ.get("ASCSYNC_PROJECT") or os.getcwd()))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
ASSETS_DIR = os.path.join(PROJECT_ROOT, "assets")
SNAPSHOT_DIR = os.path.join(PROJECT_ROOT, ".snapshot")
LOCALES_PATH = os.path.join(DATA_DIR, "locales.json")
APP_PATH = os.path.join(DATA_DIR, "app.json")
LOCK_PATH = os.path.join(PROJECT_ROOT, "assets.lock.json")
REQUEST_LOG_PATH = os.path.join(PROJECT_ROOT, ".requests.log")


def read_json(path: str, default: Any = None) -> Any:
    if not os.path.exists(path):
        if default is None:
            raise SystemExit(f"File missing: {path}")
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def data_path(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


def snapshot_path(*parts: str) -> str:
    return os.path.join(SNAPSHOT_DIR, *parts)


def asset_path(*parts: str) -> str:
    return os.path.join(ASSETS_DIR, *parts)


def rel_to_asc(path: str) -> str:
    """Path relative to the project directory — for readable reports."""
    try:
        return os.path.relpath(path, PROJECT_ROOT)
    except ValueError:
        return path


def load_locales() -> list:
    """The one language list for ALL domains (data/locales.json)."""
    data = read_json(LOCALES_PATH)
    locales = data.get("locales") or []
    if not locales:
        raise SystemExit(f"{LOCALES_PATH}: 'locales' is empty.")
    return list(locales)


_app_config: Any = None


def load_app_config() -> dict:
    """data/app.json — bundleId, prefix for asset filenames, categories, ..."""
    global _app_config
    if _app_config is None:
        _app_config = read_json(APP_PATH)
    return _app_config


def forget_app_config() -> None:
    """Drop the cache — needed when app.json changes within one process."""
    global _app_config
    _app_config = None
