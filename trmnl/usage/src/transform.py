"""OpenCode usage tracker transform for trmnl-opencode.

The polled payload is the OpenCode server's session list
(GET /api/session?limit=10000: {"data": [...], "cursor": ...}) plus the
`trmnl` namespace. This transform computes hourly (24h), daily (7d) and
monthly (30d) usage buckets plus token and cost statistics.

All computation is local (stdlib only) — no network calls. Sessions are
bucketed in the timezone from the `timezone` custom field so the card
matches the OpenCode server's local clock.
"""

import datetime
import json
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


def _text(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _tz(name):
    """Build a tzinfo from an IANA name, falling back to UTC."""
    if ZoneInfo is not None:
        try:
            return ZoneInfo(_text(name) or "UTC")
        except Exception:
            pass
    return datetime.timezone.utc


def _resolved_tz_name(name):
    """The name actually used; UTC if the configured zone could not be loaded."""
    if ZoneInfo is not None:
        try:
            ZoneInfo(_text(name) or "UTC")
            return _text(name) or "UTC"
        except Exception:
            pass
    return "UTC"


def _cost(session):
    try:
        return float(session.get("cost") or 0)
    except (TypeError, ValueError):
        return 0.0


def _tokens(session):
    """Total tokens across all streams for one session."""
    t = session.get("tokens") or {}
    if not isinstance(t, dict):
        return 0
    cache = t.get("cache") or {}
    if not isinstance(cache, dict):
        cache = {}
    return int(
        (t.get("input") or 0)
        + (t.get("output") or 0)
        + (t.get("reasoning") or 0)
        + (cache.get("read") or 0)
        + (cache.get("write") or 0)
    )


def _token_streams(session):
    """Individual token streams for one session (for stats)."""
    t = session.get("tokens") or {}
    if not isinstance(t, dict):
        return {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    cache = t.get("cache") or {}
    if not isinstance(cache, dict):
        cache = {}
    return {
        "input": int(t.get("input") or 0),
        "output": int(t.get("output") or 0),
        "reasoning": int(t.get("reasoning") or 0),
        "cache_read": int(cache.get("read") or 0),
        "cache_write": int(cache.get("write") or 0),
    }


def _created_ms(session):
    try:
        t = session.get("time") or {}
        return int(t.get("created") or 0)
    except (TypeError, ValueError):
        return 0


def _model_id(session):
    m = session.get("model") or {}
    if not isinstance(m, dict):
        return "unknown"
    return _text(m.get("id") or m.get("providerID") or "unknown")


def _fmt_tokens(n):
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(int(n))


def _fmt_cost(cost):
    return f"${cost:,.2f}"


def _bucket_start(now_local, delta):
    """Start of a bucket as local datetime."""
    return (now_local - delta).replace(minute=0, second=0, microsecond=0)


def _day_start(now_local):
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0)


def _aggregate(sessions):
    """Aggregate a list of sessions into totals."""
    agg = {"sessions": len(sessions), "cost": 0.0, "tokens": 0, "streams": None}
    streams = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
    for s in sessions:
        agg["cost"] += _cost(s)
        agg["tokens"] += _tokens(s)
        for key, value in _token_streams(s).items():
            streams[key] += value
    agg["streams"] = streams
    return agg


def _models(sessions, limit=5):
    """Per-model breakdown, sorted by cost desc."""
    by_model = {}
    for s in sessions:
        mid = _model_id(s)
        entry = by_model.setdefault(mid, {"sessions": 0, "cost": 0.0, "tokens": 0})
        entry["sessions"] += 1
        entry["cost"] += _cost(s)
        entry["tokens"] += _tokens(s)
    ranked = sorted(by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True)
    total_cost = sum(v["cost"] for _, v in ranked) or 1.0
    out = []
    for mid, v in ranked[:limit]:
        out.append(
            {
                "name": mid,
                "sessions": v["sessions"],
                "cost": v["cost"],
                "cost_display": _fmt_cost(v["cost"]),
                "tokens": v["tokens"],
                "tokens_display": _fmt_tokens(v["tokens"]),
                "pct": round(v["cost"] / total_cost * 100),
            }
        )
    return out


def _build_series(sessions, now_local, bucket_size, count, label_fmt):
    """Build oldest->newest series of buckets of `bucket_size` (a timedelta)."""
    series = []
    for i in range(count - 1, -1, -1):
        start = _bucket_start(now_local, bucket_size * i)
        end = start + bucket_size
        window = [
            s
            for s in sessions
            if start.timestamp() * 1000 <= _created_ms(s) < end.timestamp() * 1000
        ]
        agg = _aggregate(window)
        series.append(
            {
                "label": start.strftime(label_fmt),
                "sessions": agg["sessions"],
                "cost": agg["cost"],
                "cost_display": _fmt_cost(agg["cost"]),
                "tokens": agg["tokens"],
                "tokens_display": _fmt_tokens(agg["tokens"]),
            }
        )
    # Normalize cost to a 0-100 percentage of the busiest bucket, so Liquid
    # templates can render bar widths without arithmetic.
    max_cost = max((item["cost"] for item in series), default=0.0)
    for item in series:
        item["pct"] = round(item["cost"] / max_cost * 100) if max_cost > 0 else 0
    return series


def _summary(sessions, now_local, tz):
    """Aggregate windows: today / 7d / 30d / all-time."""
    day_start = _day_start(now_local)
    week_start = _bucket_start(now_local, datetime.timedelta(days=7))
    month_start = _bucket_start(now_local, datetime.timedelta(days=30))

    def since(cutoff):
        return [s for s in sessions if _created_ms(s) >= cutoff.timestamp() * 1000]

    def render(agg, label):
        return {
            "label": label,
            "sessions": agg["sessions"],
            "cost": agg["cost"],
            "cost_display": _fmt_cost(agg["cost"]),
            "tokens": agg["tokens"],
            "tokens_display": _fmt_tokens(agg["tokens"]),
        }

    return [
        render(_aggregate(since(day_start)), "Today"),
        render(_aggregate(since(week_start)), "Last 7 days"),
        render(_aggregate(since(month_start)), "Last 30 days"),
        render(_aggregate(sessions), "All time"),
    ]


def run(input_data):
    url = ""
    api_key = ""
    timezone = "UTC"
    try:
        settings = input_data["trmnl"]["plugin_settings"]["custom_fields_values"]
        url = _text(settings.get("url"))
        api_key = _text(settings.get("api_key"))
        timezone = _text(settings.get("timezone")) or "UTC"
    except (KeyError, TypeError, AttributeError):
        pass

    if not url:
        return {"error": "Set the url custom field to your OpenCode server address."}

    data = input_data.get("data") if isinstance(input_data, dict) else None
    if not isinstance(data, list):
        return {
            "error": "Could not read the session list. Check the url custom field."
        }

    tz = _tz(timezone)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    now_local = now_utc.astimezone(tz)
    resolved_timezone = _resolved_tz_name(timezone)

    total_agg = _aggregate(data)
    if total_agg["sessions"] == 0:
        return {"error": "No sessions found on this OpenCode server."}

    streams = total_agg["streams"]
    return {
        "generated_at": now_local.strftime("%a %d %b %H:%M"),
        "timezone": resolved_timezone,
        "sessions_total": total_agg["sessions"],
        "totals": _summary(data, now_local, tz),
        "hourly": _build_series(
            data, now_local, datetime.timedelta(hours=1), 24, "%H:%M"
        ),
        "daily": _build_series(
            data, now_local, datetime.timedelta(days=1), 7, "%a %d"
        ),
        "monthly": _build_series(
            data, now_local, datetime.timedelta(days=1), 30, "%d %b"
        ),
        "streams": {
            "input": streams["input"],
            "output": streams["output"],
            "reasoning": streams["reasoning"],
            "cache_read": streams["cache_read"],
            "cache_write": streams["cache_write"],
            "input_display": _fmt_tokens(streams["input"]),
            "output_display": _fmt_tokens(streams["output"]),
            "reasoning_display": _fmt_tokens(streams["reasoning"]),
            "cache_read_display": _fmt_tokens(streams["cache_read"]),
            "cache_write_display": _fmt_tokens(streams["cache_write"]),
        },
        "models": _models(data),
        "api_key_set": bool(api_key),
    }


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    print(json.dumps(run(payload)))
