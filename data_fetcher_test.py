#############################################################################
# data_fetcher_test.py
#
# This file contains tests for data_fetcher.py using mocking to avoid 
# real database and API calls (Safe for GitHub Actions).
#############################################################################
import unittest
from unittest.mock import patch
import data_fetcher

class TestDataFetcher(unittest.TestCase):

    @patch('data_fetcher.bq_client')
    def test_get_products(self, mock_bq):
        """Tests fetching a list of products using a mocked BigQuery client."""
        # Create fake database rows
        mock_bq.query.return_value.result.return_value = [
            {"product_id": "h1", "product_name": "Mock Hat", "price": 20.0}
        ]
        
        products = data_fetcher.get_products()
        
        self.assertIsInstance(products, list)
        self.assertEqual(len(products), 1)
        self.assertEqual(products[0]["product_id"], "h1")
        mock_bq.query.assert_called_once() # Ensures BigQuery was "called"

    @patch('data_fetcher.bq_client')
    def test_get_product_by_id(self, mock_bq):
        """Tests fetching a single product."""
        test_id = "hat-001" 
        # Fake the response for a specific ID
        mock_bq.query.return_value.result.return_value = [
            {"product_id": test_id, "product_name": "Test Hat"}
        ]
        
        product = data_fetcher.get_product(test_id)
        
        self.assertIsNotNone(product)
        self.assertEqual(product["product_id"], test_id)

    @patch('data_fetcher.bq_client')
    def test_get_user_not_found(self, mock_bq):
        """Tests that a non-existent user returns None."""
        # Fake an empty database response
        mock_bq.query.return_value.result.return_value = []
        
        user = data_fetcher.get_user("fake_user_id_999")
        self.assertIsNone(user)

    @patch('data_fetcher.bq_client')
    def test_get_cart_returns_list(self, mock_bq):
        """Tests that cart retrieval returns a list."""
        mock_bq.query.return_value.result.return_value = [{"product_id": "h1", "qty": 1}]
        
        cart = data_fetcher.get_cart("test_user")
        self.assertIsInstance(cart, list)

    @patch('data_fetcher.bq_client')
    def test_get_orders(self, mock_bq):
        """Tests that order retrieval returns a list."""
        mock_bq.query.return_value.result.return_value = [{"order_id": "123", "user_id": "test_user"}]
        
        orders = data_fetcher.get_orders("test_user")
        self.assertIsInstance(orders, list)

    @patch('data_fetcher.genai_model')
    @patch('data_fetcher.get_orders')
    @patch('data_fetcher.get_cart')
    def test_genai_recommendation_structure(self, mock_get_cart, mock_get_orders, mock_genai):
        """Gemini output is parsed into catalog-grounded recommendation dicts."""
        mock_get_cart.return_value = []
        mock_get_orders.return_value = []
        products = [
            {"product_id": "h1", "name": "A", "style": "Snapback", "color": "Red", "price": 30, "stock": 5},
            {"product_id": "h2", "name": "B", "style": "Fitted", "color": "Black", "price": 35, "stock": 5},
        ]
        mock_genai.generate_content.return_value.text = (
            '{"recommendations": [{"product_id": "h2", "style_note": "Wear low", "reason": "Fits you"}]}'
        )

        result = data_fetcher.get_genai_recommendations("test_user", products=products)

        self.assertEqual(result["user_id"], "test_user")
        self.assertEqual(result["source"], "gemini")
        self.assertEqual(result["recommendations"][0]["product_id"], "h2")
        self.assertEqual(result["recommendations"][0]["name"], "B")

    @patch('data_fetcher.genai_model')
    @patch('data_fetcher.get_orders')
    @patch('data_fetcher.get_cart')
    def test_genai_falls_back_on_bad_output(self, mock_get_cart, mock_get_orders, mock_genai):
        """Non-JSON model output triggers the rule-based fallback, never a crash."""
        mock_get_cart.return_value = []
        mock_get_orders.return_value = []
        mock_genai.generate_content.return_value.text = "Sorry, here is some prose."
        products = [{"product_id": "h1", "name": "A", "style": "Snapback", "color": "Red", "price": 30, "stock": 5}]

        result = data_fetcher.get_genai_recommendations("u", products=products)

        self.assertEqual(result["source"], "fallback")
        self.assertEqual([r["product_id"] for r in result["recommendations"]], ["h1"])

    def test_parse_rejects_hallucinated_and_duplicate_ids(self):
        candidates = [{"product_id": "h1", "name": "A"}, {"product_id": "h2", "name": "B"}]
        raw = (
            '{"recommendations": ['
            '{"product_id": "h1", "style_note": "x", "reason": "y"},'
            '{"product_id": "h1", "style_note": "x", "reason": "y"},'
            '{"product_id": "ghost", "style_note": "x", "reason": "y"}]}'
        )
        recs = data_fetcher.parse_recommendations(raw, candidates)
        self.assertEqual([r["product_id"] for r in recs], ["h1"])

    def test_parse_caps_at_three(self):
        candidates = [{"product_id": f"h{i}", "name": str(i)} for i in range(6)]
        raw = '{"recommendations": [%s]}' % ",".join(
            '{"product_id": "h%d", "style_note": "", "reason": ""}' % i for i in range(6))
        self.assertEqual(len(data_fetcher.parse_recommendations(raw, candidates)), 3)

    def test_candidates_exclude_owned_and_out_of_stock(self):
        products = [
            {"product_id": "h1", "name": "A", "stock": 5},
            {"product_id": "h2", "name": "B", "stock": 0},
            {"product_id": "h3", "name": "C", "stock": 2},
        ]
        got = data_fetcher.build_candidate_catalog(products, owned_ids={"h1"})
        self.assertEqual([c["product_id"] for c in got], ["h3"])

    def test_fallback_prefers_owned_style(self):
        products = [
            {"product_id": "h1", "name": "A", "style": "Fitted", "color": "Red"},
            {"product_id": "h2", "name": "B", "style": "Snapback", "color": "Black"},
            {"product_id": "h3", "name": "C", "style": "Fitted", "color": "Blue"},
        ]
        cart = [{"product_id": "h1"}]
        cands = data_fetcher.build_candidate_catalog(products, {"h1"})
        recs = data_fetcher.fallback_recommendations(cands, cart, [], products)
        self.assertEqual(recs[0]["product_id"], "h3")

    @patch('data_fetcher.create_order')
    def test_place_order_reports_failures(self, mock_create):
        mock_create.side_effect = [None, RuntimeError("bq down")]
        failed = data_fetcher.place_order("u", {"h1": 1, "h2": 2})
        self.assertEqual(failed, ["h2"])

    @patch('data_fetcher.create_order')
    def test_place_order_success(self, mock_create):
        self.assertEqual(data_fetcher.place_order("u", {"h1": 1}), [])
        mock_create.assert_called_once_with("u", "h1", 1)

if __name__ == "__main__":
    unittest.main()
