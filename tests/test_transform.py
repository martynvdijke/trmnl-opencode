"""Tests for the OpenCode usage transform.

Run with: python3 -m unittest discover -s tests
or:       python3 tests/test_transform.py
"""

import datetime
import importlib.util
import json
import os
import unittest

_USAGE_DIR = os.path.join(os.path.dirname(__file__), "..", "trmnl", "usage", "src")
_spec = importlib.util.spec_from_file_location(
    "usage_transform", os.path.join(_USAGE_DIR, "transform.py")
)
transform = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(transform)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixture.json")

FIELDS = ("url", "api_key", "timezone")


def _input(fixture, url="https://opencode.example.com", timezone="UTC"):
    with open(fixture) as fh:
        payload = json.load(fh)
    payload["trmnl"] = {
        "plugin_settings": {
            "custom_fields_values": {
                "url": url,
                "api_key": "",
                "timezone": timezone,
            }
        }
    }
    return payload


class TransformTest(unittest.TestCase):
    def test_missing_url(self):
        out = transform.run(_input(FIXTURE, url=""))
        self.assertIn("error", out)
        self.assertIn("url", out["error"])

    def test_no_data(self):
        payload = {
            "data": [],
            "trmnl": {"plugin_settings": {"custom_fields_values": dict.fromkeys(FIELDS, "")}},
        }
        payload["trmnl"]["plugin_settings"]["custom_fields_values"]["url"] = "https://x"
        out = transform.run(payload)
        self.assertIn("error", out)

    def test_buckets_shapes(self):
        out = transform.run(_input(FIXTURE))
        self.assertNotIn("error", out)
        self.assertEqual(len(out["hourly"]), 24)
        self.assertEqual(len(out["daily"]), 7)
        self.assertEqual(len(out["monthly"]), 30)
        self.assertEqual(len(out["totals"]), 4)
        for item in out["hourly"] + out["daily"] + out["monthly"]:
            self.assertIn("pct", item)
            self.assertTrue(0 <= item["pct"] <= 100)
        # bucket labels use the configured format (zero-padded HH:MM)
        for item in out["hourly"]:
            self.assertRegex(item["label"], r"^\d{2}:\d{2}$")

    def test_aggregates_are_monotonic(self):
        out = transform.run(_input(FIXTURE))
        totals = out["totals"]
        # session count must be non-decreasing across Today -> 7d -> 30d -> All
        counts = [t["sessions"] for t in totals]
        self.assertEqual(counts, sorted(counts))
        # All-time matches the fixture sum
        with open(FIXTURE) as fh:
            fixture_data = json.load(fh)["data"]
        self.assertEqual(counts[3], len(fixture_data))

    def test_missing_model_and_tokens_tolerated(self):
        out = transform.run(_input(FIXTURE))
        self.assertNotIn("error", out)
        for m in out["models"]:
            self.assertIn("name", m)

    def test_timezone_bucketing(self):
        ams = transform.run(_input(FIXTURE, timezone="Europe/Amsterdam"))
        utc = transform.run(_input(FIXTURE, timezone="UTC"))
        self.assertEqual(ams["timezone"], "Europe/Amsterdam")
        self.assertEqual(utc["timezone"], "UTC")
        # same sessions, different labels at hour boundaries possible; just verify runs
        self.assertNotIn("error", ams)
        self.assertNotIn("error", utc)

    def test_unknown_timezone_falls_back(self):
        out = transform.run(_input(FIXTURE, timezone="Mars/Olympus"))
        self.assertEqual(out["timezone"], "UTC")


if __name__ == "__main__":
    unittest.main()
