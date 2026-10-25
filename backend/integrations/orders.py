"""Allowlisted external orders feeds and their isolated mapping scopes."""

from pathlib import Path
import re

import yaml

FEEDS = Path(__file__).resolve().parents[2] / "config" / "external_feeds.yaml"
_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def feed_spec(feed_id: str) -> dict:
    if not isinstance(feed_id, str) or not _ID.fullmatch(feed_id):
        raise ValueError("unknown external feed ID")
    raw = yaml.safe_load(FEEDS.read_text(encoding="utf-8")) or {}
    feeds = raw.get("feeds") or {}
    if feed_id not in feeds:
        raise ValueError("unknown external feed ID")
    spec = feeds[feed_id]
    if not isinstance(spec, dict) or spec.get("contract_name") != "default":
        raise ValueError("invalid external feed config")
    return spec


def feed_scope(base_env: str, feed_id: str) -> str:
    feed_spec(feed_id)
    return f"{base_env}:external:{feed_id}"


def validate_batch_id(batch_id: str) -> str:
    if not isinstance(batch_id, str) or not _ID.fullmatch(batch_id):
        raise ValueError("invalid external batch ID")
    return batch_id
