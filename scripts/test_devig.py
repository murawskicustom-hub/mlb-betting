"""
Unit tests for devig.py.
Run with: python test_devig.py
"""

import sys
import unittest

sys.path.insert(0, '.')
from devig import (
    american_to_decimal,
    american_to_probability,
    probability_to_american,
    devig_two_way,
    edge_percent,
)


class TestAmericanToDecimal(unittest.TestCase):
    def test_negative(self):
        result = american_to_decimal(-150)
        self.assertAlmostEqual(result, 1.666667, places=5,
                               msg="-150 should give decimal ≈1.666667")

    def test_positive(self):
        result = american_to_decimal(130)
        self.assertAlmostEqual(result, 2.300000, places=5,
                               msg="+130 should give decimal 2.30")

    def test_even_money(self):
        self.assertAlmostEqual(american_to_decimal(100), 2.0, places=5)

    def test_heavy_favourite(self):
        # -300 → 100/300 + 1 = 1.333...
        self.assertAlmostEqual(american_to_decimal(-300), 1.333333, places=5)


class TestAmericanToProbability(unittest.TestCase):
    def test_negative(self):
        result = american_to_probability(-150)
        self.assertAlmostEqual(result, 0.6, places=4,
                               msg="-150 should imply ≈60% probability")

    def test_positive(self):
        result = american_to_probability(130)
        self.assertAlmostEqual(result, 0.434783, places=4,
                               msg="+130 should imply ≈43.48% probability")

    def test_even_money(self):
        self.assertAlmostEqual(american_to_probability(100), 0.5, places=5)

    def test_sum_reflects_overround(self):
        # -110 / -110 standard vig: both sides ~52.38% — should sum > 1
        p1 = american_to_probability(-110)
        p2 = american_to_probability(-110)
        self.assertGreater(p1 + p2, 1.0, msg="Vigged market should sum to > 1")


class TestProbabilityToAmerican(unittest.TestCase):
    def test_favourite(self):
        result = probability_to_american(0.6)
        self.assertEqual(result, -150,
                         msg="60% probability should give -150")

    def test_underdog(self):
        # 0.4348 → +130 approximately
        result = probability_to_american(0.4348)
        self.assertAlmostEqual(result, 130, delta=2,
                               msg="43.48% probability should give ≈+130")

    def test_even_money(self):
        result = probability_to_american(0.5)
        self.assertEqual(result, -100)

    def test_extreme_clip(self):
        # Should not raise even at edge cases
        _ = probability_to_american(0.0001)
        _ = probability_to_american(0.9999)


class TestRoundTrip(unittest.TestCase):
    def test_negative_roundtrip(self):
        """Convert -150 → probability → back to American → -150"""
        original = -150
        prob = american_to_probability(original)
        back = probability_to_american(prob)
        self.assertAlmostEqual(back, original, delta=1,
                               msg=f"-150 round-trip should return -150, got {back}")

    def test_positive_roundtrip(self):
        original = 130
        prob = american_to_probability(original)
        back = probability_to_american(prob)
        self.assertAlmostEqual(back, original, delta=1,
                               msg=f"+130 round-trip should return +130, got {back}")


class TestDevigTwoWay(unittest.TestCase):
    def test_sum_to_one(self):
        dv_a, dv_b = devig_two_way(0.60, 0.435)
        self.assertAlmostEqual(dv_a + dv_b, 1.0, places=10,
                               msg="Devigged probabilities must sum to 1.0")

    def test_proportionality(self):
        # Heavier favourite should still be heavier after devig
        dv_a, dv_b = devig_two_way(0.60, 0.435)
        self.assertGreater(dv_a, dv_b,
                           msg="Higher raw prob should stay higher after devig")

    def test_standard_vig(self):
        # -110 / -110 → both sides 52.38% raw → devigged both 50%
        p = american_to_probability(-110)  # ≈ 0.5238
        dv_a, dv_b = devig_two_way(p, p)
        self.assertAlmostEqual(dv_a, 0.5, places=4)
        self.assertAlmostEqual(dv_b, 0.5, places=4)


class TestEdgePercent(unittest.TestCase):
    def test_positive_edge(self):
        """50% true chance, offered at +130 (decimal 2.30) → +15% edge"""
        # fair price that gives exactly 50% prob: +100 or -100
        # But spec says: fair=50% chance, offered=+130
        # Use +100 as "fair price at 50%" for the formula
        result = edge_percent(offered_price_american=130, fair_price_american=100)
        # fair_prob = 0.5, offered_dec = 2.30
        # edge = (0.5 × 2.30 - 1) × 100 = +15.0
        self.assertAlmostEqual(result, 15.0, places=2,
                               msg="+130 offered at 50% true prob should give +15% edge")

    def test_negative_edge(self):
        """50% true chance, offered at -150 (decimal 1.667) → -16.65% edge"""
        result = edge_percent(offered_price_american=-150, fair_price_american=100)
        # fair_prob = 0.5, offered_dec = 1.6667
        # edge = (0.5 × 1.6667 - 1) × 100 = -16.65
        self.assertAlmostEqual(result, -16.65, delta=0.1,
                               msg="-150 offered at 50% true prob should give ≈-16.65% edge")

    def test_zero_edge_at_fair_price(self):
        """If offered price == fair price, edge should be 0"""
        result = edge_percent(offered_price_american=-150, fair_price_american=-150)
        self.assertAlmostEqual(result, 0.0, places=3,
                               msg="Edge should be 0 when offered == fair")

    def test_small_positive_edge(self):
        """Realistic example: fair -118, offered -110 → small +edge"""
        result = edge_percent(offered_price_american=-110, fair_price_american=-118)
        self.assertGreater(result, 0, msg="-110 vs fair -118 should be positive edge")


if __name__ == '__main__':
    loader  = unittest.TestLoader()
    suite   = unittest.TestSuite()
    for cls in [TestAmericanToDecimal, TestAmericanToProbability,
                TestProbabilityToAmerican, TestRoundTrip,
                TestDevigTwoWay, TestEdgePercent]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
