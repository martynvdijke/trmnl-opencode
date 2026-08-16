"""OpenCode Go usage limits transform for trmnl-opencode.

The polled payload is the OpenCode Go usage endpoint
(GET https://opencode.ai/zen/go/v1/usage, `Authorization: Bearer <api_key>`):

    {
      "usage": {
        "rolling": {"status": "ok", "percent": 2,  "resetsAt": "2026-08-16T12:41:17.876Z"},
        "weekly":  {"status": "ok", "percent": 55, "resetsAt": "2026-08-17T00:00:00.876Z"},
        "monthly": {"status": "ok", "percent": 36, "resetsAt": "2026-09-03T14:46:26.876Z"}
      }
    }

Each limit reports how much of the allowance has been consumed (`percent`,
0-100) and when it resets. `status` is "ok" or "rate-limited". All computation
is local (stdlib only) — no network calls. Reset times are rendered in the
timezone from the `timezone` custom field so the card matches the user's clock.
"""

import datetime
import json
import sys

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None

# Display order and labels for the three limit windows.
LIMIT_ORDER = ("rolling", "weekly", "monthly")
LABELS = {
    "rolling": ("Rolling", "rolling window"),
    "weekly": ("Weekly", "weekly"),
    "monthly": ("Monthly", "monthly"),
}


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


def _parse_iso(value):
    """Parse an ISO-8601 UTC timestamp into an aware datetime."""
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(_text(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _fmt_delta(delta):
    """Human duration like '2h 40m', '1d 3h' or '5m'."""
    total = max(int(delta.total_seconds()), 0)
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _level(status, percent):
    """Semantic level for Liquid: success / warning / error."""
    if status == "rate-limited" or percent >= 90:
        return "error"
    if percent >= 70:
        return "warning"
    return "success"


def _format_limit(key, value, now_utc, tz):
    status = _text(value.get("status")) or "ok"
    try:
        percent = int(value.get("percent") or 0)
    except (TypeError, ValueError):
        percent = 0
    percent = max(0, min(percent, 100))
    label, period = LABELS.get(key, (key, key))
    item = {
        "key": key,
        "label": label,
        "period": period,
        "status": status,
        "status_label": "OK" if status == "ok" else "LIMIT",
        "percent": percent,
        "pct": percent,
        "level": _level(status, percent),
        "resets_at": "",
        "resets_in": "",
    }
    resets_at = _parse_iso(value.get("resetsAt"))
    if resets_at is not None:
        item["resets_at"] = resets_at.astimezone(tz).strftime("%a %d %b %H:%M")
        item["resets_in"] = _fmt_delta(resets_at - now_utc)
    return item


def run(input_data):
    api_key = ""
    timezone = "UTC"
    try:
        settings = input_data["trmnl"]["plugin_settings"]["custom_fields_values"]
        api_key = _text(settings.get("api_key"))
        timezone = _text(settings.get("timezone")) or "UTC"
    except (KeyError, TypeError, AttributeError):
        pass

    data = input_data.get("data") if isinstance(input_data, dict) else None
    if isinstance(data, dict) and isinstance(data.get("usage"), dict):
        usage = data["usage"]
    else:
        # Object responses are kept at the top level by TRMNL. Array
        # responses are wrapped under `data`, so accept both shapes.
        usage = input_data.get("usage") if isinstance(input_data, dict) else None
    if not isinstance(usage, dict) or not any(k in usage for k in LIMIT_ORDER):
        return {
            "error": "Could not read usage limits from OpenCode Go. Check the api_key custom field."
        }

    tz = _tz(timezone)
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    limits = [
        _format_limit(key, usage[key], now_utc, tz)
        for key in LIMIT_ORDER
        if isinstance(usage.get(key), dict)
    ]
    if not limits:
        return {"error": "OpenCode Go returned no usage limits."}

    worst = max(limits, key=lambda item: item["percent"])
    return {
        "generated_at": now_utc.astimezone(tz).strftime("%a %d %b %H:%M"),
        "timezone": _resolved_tz_name(timezone),
        "limits": limits,
        "worst": worst,
        "worst_label": worst["label"],
        "worst_percent": worst["percent"],
        "worst_level": worst["level"],
        "worst_status": worst["status_label"],
        "worst_resets_in": worst["resets_in"],
        "all_ok": all(item["status"] == "ok" for item in limits),
        "api_key_set": bool(api_key),
    }


if __name__ == "__main__":
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    print(json.dumps(run(payload)))
