"""seats/seats.json is a synced copy of the orchestrator's seat file. These checks keep the
two ladders from drifting: the tier rules there must equal ours, and every model we pick
must be a legal seat in the pool we bill it to."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pr_context  # noqa: E402
import review  # noqa: E402
import usage  # noqa: E402

SEATS = json.loads((ROOT / "seats" / "seats.json").read_text(encoding="utf-8"))


class SeatsSync(unittest.TestCase):
    def test_tier_rules_match(self):
        rules = SEATS["tier_rules"]
        self.assertEqual(rules["non_code"], pr_context.NON_CODE)
        self.assertEqual(rules["risk_paths"], {name: rx.pattern for name, rx in pr_context.RISK})
        self.assertEqual({k: tuple(v) for k, v in rules["categories"].items()},
                         {k: v for k, v in pr_context.TIER_CATS.items() if k in rules["categories"]})
        self.assertEqual(rules["strong_at"], review.TIER_THRESHOLD)
        self.assertEqual(rules["critical_at"], review.CRITICAL_THRESHOLD)
        self.assertEqual(SEATS["tiers"], ["light", "cheap", "strong", "critical"])

    def test_every_picked_model_is_a_seat_in_its_pool(self):
        seats = {(s["engine"], s["model"]): s for s in SEATS["seats"]}
        for engine, tiers in review.MODEL_TIERS.items():
            for level, (model, _effort) in tiers.items():
                with self.subTest(engine=engine, level=level, model=model):
                    seat = seats.get((engine, model))
                    self.assertIsNotNone(seat, "not in seats/seats.json")
                    self.assertIn(seat["status"], ("legal", "qualified", "default"))
                    self.assertEqual(usage.pool_of(engine, model), seat["pool"])

    def test_pool_of_agrees_with_every_seat(self):
        for s in SEATS["seats"]:
            with self.subTest(model=s["model"]):
                self.assertEqual(usage.pool_of(s["engine"], s["model"]), s["pool"])


if __name__ == "__main__":
    unittest.main()
