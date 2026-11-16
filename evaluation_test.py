#############################################################################
# evaluation_test.py
#
# Tests the Style Coach evaluation harness offline (no BigQuery, no Gemini).
#############################################################################
import json
import os
import unittest

from evaluation import run_eval

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evaluation")
CATALOG = json.load(open(os.path.join(HERE, "sample_catalog.json")))
PERSONAS = json.load(open(os.path.join(HERE, "personas.json")))


def taste_aware_model(prompt):
    """Pretend-Gemini that picks Snapbacks first (what the snapback persona likes)."""
    line = prompt.split("from this list:")[1].strip().splitlines()[0]
    cands = sorted(json.loads(line), key=lambda c: c["style"] != "Snapback")[:3]
    return json.dumps({"recommendations": [
        {"product_id": c["product_id"], "style_note": "x", "reason": "y"} for c in cands]})


class TestEvaluation(unittest.TestCase):

    def test_materialize_builds_history_from_traits(self):
        persona = next(p for p in PERSONAS if p["id"] == "mixed_cart_and_orders")
        cart, orders = run_eval.materialize(persona, CATALOG)
        self.assertEqual(len(cart), 1)
        self.assertEqual(len(orders), 1)
        self.assertEqual(cart[0]["style"], "Trucker")

    def test_materialize_unsatisfiable_persona_raises(self):
        persona = {"id": "x", "owns": [{"style": "Fedora"}], "likes": {"styles": [], "colors": []}}
        with self.assertRaises(ValueError):
            run_eval.materialize(persona, CATALOG)

    def test_owns_all_but_leaves_small_pool(self):
        persona = next(p for p in PERSONAS if p["id"] == "almost_everything_owned")
        _, orders = run_eval.materialize(persona, CATALOG)
        self.assertEqual(len(orders), len(CATALOG) - 2)

    def test_score_counts_matches(self):
        by_id = {"a": {"style": "Snapback", "color": "Red"}, "b": {"style": "Fitted", "color": "Blue"}}
        likes = {"styles": ["Snapback"], "colors": []}
        precision, hit = run_eval.score([{"product_id": "a"}, {"product_id": "b"}], by_id, likes)
        self.assertEqual((precision, hit), (0.5, 1.0))

    def test_raw_output_checks_flag_bad_json_and_bad_ids(self):
        self.assertFalse(run_eval.raw_output_checks("not json", {"a"})["valid_json"])
        good = '{"recommendations": [{"product_id": "zzz", "style_note": "", "reason": ""}]}'
        checks = run_eval.raw_output_checks(good, {"a"})
        self.assertTrue(checks["valid_json"])
        self.assertEqual(checks["invalid_id_rate"], 1.0)

    def test_full_run_reliable_model(self):
        r = run_eval.evaluate(CATALOG, PERSONAS, taste_aware_model, repeats=1)
        self.assertEqual(r["gemini_reliability"]["valid_json_rate"], 1.0)
        self.assertEqual(r["gemini_reliability"]["invalid_id_rate"], 0.0)
        self.assertEqual(r["gemini_reliability"]["fallback_rate"], 0.0)
        self.assertEqual(r["skipped"], [])
        self.assertGreater(r["relevance"]["rule_based"]["precision_at_3"],
                           r["relevance"]["random"]["precision_at_3"])

    def test_full_run_broken_model_uses_fallback(self):
        r = run_eval.evaluate(CATALOG, PERSONAS, lambda prompt: "garbage", repeats=1)
        self.assertEqual(r["gemini_reliability"]["valid_json_rate"], 0.0)
        self.assertEqual(r["gemini_reliability"]["fallback_rate"], 1.0)
        # the app falls back to rule-based picks, so users still get relevant hats
        self.assertEqual(r["relevance"]["gemini"], r["relevance"]["rule_based"])

    def test_report_mentions_limits(self):
        r = run_eval.evaluate(CATALOG, PERSONAS, taste_aware_model, repeats=1)
        md = run_eval.render_markdown(r, "test")
        self.assertIn("Limits of this evaluation", md)
        self.assertIn("Precision@3", md)


if __name__ == "__main__":
    unittest.main()
