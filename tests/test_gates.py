from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers
from ctxlib import CtxError, gates


class RoleBandsTest(unittest.TestCase):
    EXPECTED = {
        # window: (worker, planner, coordinator)
        1_000_000: (25.0, 25.0, 25.0),
        258_400: (75.0, 75.0, 75.0),
        200_000: (75.0, 75.0, 75.0),
        100_000: (80.0, 80.0, 80.0),
    }

    def test_bands_per_role(self):
        for window, (worker, planner, coord) in self.EXPECTED.items():
            with self.subTest(window=window):
                self.assertEqual(gates.band_for(gates.WORKER, window), worker)
                self.assertEqual(gates.band_for(gates.PLANNER, window), planner)
                self.assertEqual(gates.band_for(gates.COORDINATOR, window), coord)

    def test_planner_budgets_like_worker(self):
        for window in (1_000_000, 600_000, 599_999, 150_000, 149_999, 1):
            self.assertEqual(gates.band_for(gates.PLANNER, window),
                             gates.band_for(gates.WORKER, window))

    def test_unknown_role_rejected(self):
        with self.assertRaises(ValueError):
            gates.band_for("reviewer", 1_000_000)


class ResolveGateTest(unittest.TestCase):
    def test_explicit_gate_wins(self):
        for role in (None,) + gates.ROLES:
            self.assertEqual(gates.resolve_gate(role, 1_000_000, 50.0), (50.0, "--gate"))

    def test_role_derives_gate(self):
        gate, source = gates.resolve_gate(gates.COORDINATOR, 1_000_000, None)
        self.assertEqual(gate, 25.0)
        self.assertIn("coordinator band", source)

    def test_no_role_no_gate(self):
        self.assertEqual(gates.resolve_gate(None, 1_000_000, None), (None, "unset"))


class BandsFileTest(unittest.TestCase):
    """<CTXCHECK_HOME>/bands.json replaces the built-in table."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = mock.patch.dict(os.environ, {"CTXCHECK_HOME": tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, data):
        text = data if isinstance(data, str) else json.dumps(data)
        (self.home / "bands.json").write_text(text, encoding="utf-8")

    def test_no_file_uses_built_in(self):
        self.assertEqual(gates.load_bands(), (gates.BANDS, "built-in"))

    def test_file_replaces_bands(self):
        self.write([{"min_window": 0, "worker": 85},
                    {"min_window": 500_000, "worker": 30, "coordinator": 20}])
        self.assertEqual(gates.band_for(gates.WORKER, 1_000_000), 30.0)
        self.assertEqual(gates.band_for(gates.PLANNER, 1_000_000), 30.0)
        self.assertEqual(gates.band_for(gates.COORDINATOR, 1_000_000), 20.0)
        # coordinator defaults to worker; the 150K built-in band is gone
        self.assertEqual(gates.band_for(gates.COORDINATOR, 200_000), 85.0)
        gate, source = gates.resolve_gate(gates.WORKER, 1_000_000, None)
        self.assertEqual(gate, 30.0)
        self.assertIn("bands.json", source)

    def test_explicit_gate_still_wins(self):
        self.write([{"min_window": 0, "worker": 85}])
        self.assertEqual(gates.resolve_gate(gates.WORKER, 1_000_000, 50.0), (50.0, "--gate"))

    def test_malformed_files_rejected(self):
        for bad in ("{not json", "[]", "{}", [1],
                    [{"min_window": 100, "worker": 50}],
                    [{"min_window": 0}],
                    [{"min_window": 0, "worker": 0}],
                    [{"min_window": 0, "worker": 101}],
                    [{"min_window": 0, "worker": True}],
                    [{"min_window": -1, "worker": 50}, {"min_window": 0, "worker": 50}],
                    [{"min_window": 0, "worker": 50, "coordinator": "high"}]):
            with self.subTest(bad=bad):
                self.write(bad)
                with self.assertRaises(CtxError):
                    gates.load_bands()


class PresetsTest(unittest.TestCase):
    def test_refreshed_presets(self):
        self.assertEqual(gates.PRESETS["claude-1m"][0], 1_000_000)
        self.assertEqual(gates.PRESETS["claude-200k"][0], 200_000)
        self.assertEqual(gates.PRESETS["codex-272k-effective"][0], 258_400)
        self.assertIn(gates.DEFAULT_PRESET, gates.PRESETS)


if __name__ == "__main__":
    unittest.main()
