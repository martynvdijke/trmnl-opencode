"""Tests for the OpenCode Go usage limits transform.

Run with: python3 tests/test_limits_transform.py
"""

import importlib.util
import json
import os
import unittest

_LIMITS_DIR = os.path.join(os.path.dirname(__file__), "..", "trmnl", "limits", "src")
_spec = importlib.util.spec_from_file_location(
    "limits_transform", os.path.join(_LIMITS_DIR, "transform.py")
)
transform = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transform)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture_limits.json")


def _load_fixture():
    with open(FIXTURE) as fh:
        return json.load(fh)


def _input(payload=None, timezone="UTC", api_key="sk-test"):
    data = payload if payload is not None else _load_fixture()
    return {
        "data": data,
        "trmnl": {
            "plugin_settings": {
                "custom_fields_values": {"api_key": api_key, "timezone": timezone}
            }
        },
    }


class LimitsTransformTest(unittest.TestCase):
    def test_parses_all_limits(self):
        out = transform.run(_input())
        self.assertNotIn("error", out)
        self.assertEqual([item["key"] for item in out["limits"]], ["rolling", "weekly", "monthly"])
        self.assertEqual([item["label"] for item in out["limits"]], ["Rolling", "Weekly", "Monthly"])
        self.assertEqual([item["percent"] for item in out["limits"]], [2, 55, 36])
        for item in out["limits"]:
            self.assertEqual(item["status"], "ok")
            self.assertEqual(item["status_label"], "OK")
            self.assertEqual(item["level"], "success")

    def test_worst_is_highest_percent(self):
        out = transform.run(_input())
        self.assertEqual(out["worst_label"], "Weekly")
        self.assertEqual(out["worst_percent"], 55)
        self.assertTrue(out["all_ok"])

    def test_rate_limited_is_error(self):
        payload = _load_fixture()
        payload["usage"]["weekly"]["status"] = "rate-limited"
        out = transform.run(_input(payload))
        weekly = out["limits"][1]
        self.assertEqual(weekly["status_label"], "LIMIT")
        self.assertEqual(weekly["level"], "error")
        self.assertFalse(out["all_ok"])
        self.assertEqual(out["worst_level"], "error")

    def test_percent_clamped_and_levels(self):
        payload = _load_fixture()
        payload["usage"]["weekly"]["percent"] = 120
        payload["usage"]["monthly"]["percent"] = 75
        out = transform.run(_input(payload))
        by_key = {item["key"]: item for item in out["limits"]}
        self.assertEqual(by_key["weekly"]["percent"], 100)
        self.assertEqual(by_key["weekly"]["level"], "error")
        self.assertEqual(by_key["monthly"]["level"], "warning")
        self.assertEqual(by_key["rolling"]["level"], "success")

    def test_resets_are_formatted(self):
        out = transform.run(_input())
        for item in out["limits"]:
            self.assertIsInstance(item["resets_in"], str)
            self.assertTrue(item["resets_in"])
            self.assertRegex(item["resets_at"], r"^\w{3} \d{2} \w{3} \d{2}:\d{2}$")

    def test_missing_usage_returns_error(self):
        for payload in ({}, {"data": []}, {"data": {"other": 1}}):
            out = transform.run(_input(payload))
            self.assertIn("error", out)

    def test_unknown_timezone_falls_back(self):
        out = transform.run(_input(timezone="Mars/Olympus"))
        self.assertEqual(out["timezone"], "UTC")
        self.assertNotIn("error", out)

    def test_timezone_is_resolved(self):
        out = transform.run(_input(timezone="Europe/Amsterdam"))
        self.assertEqual(out["timezone"], "Europe/Amsterdam")
        self.assertNotIn("error", out)

    def test_api_key_flag(self):
        self.assertTrue(transform.run(_input(api_key="sk-123"))["api_key_set"])
        self.assertFalse(transform.run(_input(api_key=""))["api_key_set"])


if __name__ == "__main__":
    unittest.main()
