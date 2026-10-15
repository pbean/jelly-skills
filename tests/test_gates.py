from __future__ import annotations

import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)
from ctxlib import gates


class RoleBandsTest(unittest.TestCase):
    EXPECTED = {
        # window: (worker, planner, coordinator)
        1_000_000: (28.0, 28.0, 26.0),
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
        self.assertEqual(gate, 26.0)
        self.assertIn("coordinator band", source)

    def test_no_role_no_gate(self):
        self.assertEqual(gates.resolve_gate(None, 1_000_000, None), (None, "unset"))


class PresetsTest(unittest.TestCase):
    def test_refreshed_presets(self):
        self.assertEqual(gates.PRESETS["claude-1m"][0], 1_000_000)
        self.assertEqual(gates.PRESETS["claude-200k"][0], 200_000)
        self.assertEqual(gates.PRESETS["codex-272k-effective"][0], 258_400)
        self.assertIn(gates.DEFAULT_PRESET, gates.PRESETS)


if __name__ == "__main__":
    unittest.main()
