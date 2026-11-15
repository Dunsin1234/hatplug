#############################################################################
# data_fetcher.py
#
# Cleaned + fixed BigQuery backend for HatPlug app
#############################################################################

from google.cloud import bigquery
import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
import os
import json

# ==============================
# CONFIG
# ==============================
PROJECT_ID = os.getenv("PROJECT_ID", "oluwadunsin-adesanya-fisk")
DATASET = "hatplugquery"
LOCATION = "us-central1"

# ==============================
# CLIENTS
# ==============================
try:
    bq_client = bigquery.Client(project=PROJECT_ID)
except Exception as e:
    print(f"BigQuery Init Error: {e}")
    bq_client = None

try:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    genai_model = GenerativeModel("gemini-2.5-flash-lite")
except Exception as e:
    print(f"Vertex AI Init Error: {e}")
    genai_model = None

# ==============================
# RUN QUERY HELPER
# ==============================
def run_query(query, params=None):
    if bq_client is None:
        return []

    job_config = bigquery.QueryJobConfig(query_parameters=params or [])
    results = bq_client.query(query, job_config=job_config).result()

    return [dict(row) for row in results]

# ==============================
# PRODUCTS
# ==============================
def get_products():
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.products`
        LIMIT 100
    """
    return run_query(query)


def get_product(product_id):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.products`
        WHERE product_id = @product_id
        LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("product_id", "STRING", product_id)
    ]
    result = run_query(query, params)
    return result[0] if result else None

# ==============================
# USERS
# ==============================
def get_user(user_id):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.users`
        WHERE user_id = @user_id
        LIMIT 1
    """
    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id)
    ]
    result = run_query(query, params)
    return result[0] if result else None

# ==============================
# CART
# ==============================
def get_cart(user_id):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.cart`
        WHERE user_id = @user_id
    """
    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id)
    ]
    return run_query(query, params)


def upsert_cart_item(user_id, product_id, quantity):
    query = f"""
    MERGE `{PROJECT_ID}.{DATASET}.cart` T
    USING (
        SELECT
            @user_id AS user_id,
            @product_id AS product_id,
            @quantity AS quantity
    ) S
    ON T.user_id = S.user_id AND T.product_id = S.product_id

    WHEN MATCHED THEN
        UPDATE SET quantity = S.quantity

    WHEN NOT MATCHED THEN
        INSERT (user_id, product_id, quantity)
        VALUES (S.user_id, S.product_id, S.quantity)
    """

    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        bigquery.ScalarQueryParameter("product_id", "STRING", str(product_id)),
        bigquery.ScalarQueryParameter("quantity", "INT64", quantity),
    ]

    return run_query(query, params)


def delete_cart_item(user_id, product_id):
    query = f"""
    DELETE FROM `{PROJECT_ID}.{DATASET}.cart`
    WHERE user_id = @user_id
    AND product_id = @product_id
    """

    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        bigquery.ScalarQueryParameter("product_id", "STRING", str(product_id)),
    ]

    return run_query(query, params)


def clear_cart(user_id):
    query = f"""
    DELETE FROM `{PROJECT_ID}.{DATASET}.cart`
    WHERE user_id = @user_id
    """

    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
    ]

    return run_query(query, params)

# ==============================
# ORDERS (FIXED)
# ==============================
def get_orders(user_id):
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.orders`
        WHERE user_id = @user_id
        ORDER BY COALESCE(order_date, CURRENT_TIMESTAMP()) DESC
    """
    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id)
    ]
    return run_query(query, params)


def create_order(user_id, product_id, quantity):
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.orders`
        (user_id, product_id, quantity, order_date)
        VALUES (@user_id, @product_id, @quantity, CURRENT_TIMESTAMP())
    """

    params = [
        bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
        bigquery.ScalarQueryParameter("product_id", "STRING", str(product_id)),
        bigquery.ScalarQueryParameter("quantity", "INT64", quantity),
    ]

    return run_query(query, params)

# ==============================
# ORDERS: PLACE A WHOLE CART
# ==============================
def place_order(user_id, cart):
    """
    Persists every line of a {product_id: quantity} cart to the orders table.
    Returns the list of product_ids that failed to save (empty list = success),
    so the caller can keep the cart intact instead of silently losing an order.
    """
    failed = []
    for product_id, quantity in cart.items():
        try:
            create_order(user_id, product_id, int(quantity))
        except Exception as e:
            print(f"Order write failed for {product_id}: {e}")
            failed.append(product_id)
    return failed

# ==============================
# AI RECOMMENDATIONS
# ==============================
NUM_RECOMMENDATIONS = 3

RECOMMENDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string"},
                    "style_note": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["product_id", "style_note", "reason"],
            },
        }
    },
    "required": ["recommendations"],
}


def _owned_ids(cart, orders):
    """Product ids the user already has in their cart or has ordered."""
    ids = set()
    for item in cart or []:
        pid = item.get("product_id") or item.get("id")
        if pid:
            ids.add(str(pid))
    for order in orders or []:
        pid = order.get("product_id")
        if pid:
            ids.add(str(pid))
    return ids


def build_candidate_catalog(products, owned_ids):
    """In-stock products the user does not already own. This is the ONLY set
    the model may recommend from, so every recommendation is a real product."""
    candidates = []
    for p in products or []:
        pid = str(p.get("product_id") or p.get("id") or "")
        if not pid or pid in owned_ids:
            continue
        try:
            if int(p.get("stock", 1) or 0) <= 0:
                continue
        except (TypeError, ValueError):
            pass
        candidates.append({
            "product_id": pid,
            "name": p.get("name", ""),
            "style": p.get("style", ""),
            "color": p.get("color", ""),
            "price": p.get("price", 0),
            "description": p.get("description", ""),
        })
    return candidates


def parse_recommendations(raw_text, candidates):
    """
    Validates model output against the catalog. Drops unknown or duplicate
    product ids and caps the list at NUM_RECOMMENDATIONS. Returns [] if the
    text is not valid JSON in the expected shape.
    """
    by_id = {c["product_id"]: c for c in candidates}
    try:
        data = json.loads(raw_text)
        items = data["recommendations"] if isinstance(data, dict) else data
    except (TypeError, ValueError, KeyError):
        return []

    results, seen = [], set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        pid = str(item.get("product_id", ""))
        if pid not in by_id or pid in seen:
            continue
        seen.add(pid)
        results.append({
            "product_id": pid,
            "name": by_id[pid]["name"],
            "style_note": str(item.get("style_note", "")).strip(),
            "reason": str(item.get("reason", "")).strip(),
        })
        if len(results) == NUM_RECOMMENDATIONS:
            break
    return results


def fallback_recommendations(candidates, cart, orders, products):
    """Rule-based picks used when Gemini is unavailable or returns bad output:
    prefer styles and colors the user already shows interest in."""
    prefs = {"style": set(), "color": set()}
    owned = _owned_ids(cart, orders)
    for p in products or []:
        if str(p.get("product_id") or p.get("id")) in owned:
            for key in prefs:
                if p.get(key):
                    prefs[key].add(str(p[key]).lower())

    def score(c):
        return ((str(c["style"]).lower() in prefs["style"]) * 2
                + (str(c["color"]).lower() in prefs["color"]))

    ranked = sorted(candidates, key=score, reverse=True)[:NUM_RECOMMENDATIONS]
    return [{
        "product_id": c["product_id"],
        "name": c["name"],
        "style_note": f"{c['color']} {c['style']}".strip(),
        "reason": "Matches the styles and colors you already like."
                  if score(c) else "A popular pick from the collection.",
    } for c in ranked]


def get_genai_recommendations(user_id, cart_items=None, products=None):
    """
    Returns {"user_id", "source", "recommendations": [...]} where each
    recommendation is {product_id, name, style_note, reason} for a real,
    in-stock product the user does not already own.
    source is "gemini" or "fallback".
    """
    cart = cart_items if cart_items is not None else get_cart(user_id)
    orders = get_orders(user_id)
    catalog = products if products is not None else get_products()

    candidates = build_candidate_catalog(catalog, _owned_ids(cart, orders))
    if not candidates:
        return {"user_id": user_id, "source": "fallback", "recommendations": []}

    prompt = f"""
    You are a friendly fashion stylist for an HBCU campus hat store.

    The user's cart: {cart}
    The user's past orders: {orders}

    Recommend exactly {NUM_RECOMMENDATIONS} hats from this list of available
    products. Use ONLY product_id values from this list:
    {json.dumps(candidates)}

    For each, give a short style_note (how to wear it) and a reason it fits
    this user's taste. If the user has no history, pick a varied starter set.
    """

    recommendations = []
    if genai_model is not None:
        try:
            response = genai_model.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    response_mime_type="application/json",
                    response_schema=RECOMMENDATION_SCHEMA,
                    temperature=0.7,
                ),
            )
            recommendations = parse_recommendations(response.text, candidates)
        except Exception as e:
            print(f"Gemini recommendation error: {e}")

    source = "gemini"
    if not recommendations:
        source = "fallback"
        recommendations = fallback_recommendations(candidates, cart, orders, catalog)

    return {"user_id": user_id, "source": source, "recommendations": recommendations}

# ==============================
# Q&A
# ==============================

def get_questions(product_id):
    """Returns all top-level questions for a product, newest first."""
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.product_qa`
        WHERE product_id = @product_id
          AND parent_question_id IS NULL
          AND parent_answer_id IS NULL
        ORDER BY created_at DESC
    """
    params = [bigquery.ScalarQueryParameter("product_id", "STRING", product_id)]
    return run_query(query, params)


def get_answers(question_id):
    """Returns all answers for a given question."""
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.product_qa`
        WHERE parent_question_id = @question_id
          AND parent_answer_id IS NULL
        ORDER BY created_at ASC
    """
    params = [bigquery.ScalarQueryParameter("question_id", "STRING", question_id)]
    return run_query(query, params)


def get_replies(answer_id):
    """Returns all thread replies for a given answer."""
    query = f"""
        SELECT *
        FROM `{PROJECT_ID}.{DATASET}.product_qa`
        WHERE parent_answer_id = @answer_id
        ORDER BY created_at ASC
    """
    params = [bigquery.ScalarQueryParameter("answer_id", "STRING", answer_id)]
    return run_query(query, params)


def add_question(product_id, question_text, author):
    """Inserts a new question."""
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.product_qa`
          (qa_id, product_id, author, body, parent_question_id, parent_answer_id, created_at)
        VALUES
          (GENERATE_UUID(), @product_id, @author, @body, NULL, NULL, CURRENT_TIMESTAMP())
    """
    params = [
        bigquery.ScalarQueryParameter("product_id", "STRING", product_id),
        bigquery.ScalarQueryParameter("author",     "STRING", author),
        bigquery.ScalarQueryParameter("body",       "STRING", question_text),
    ]
    run_query(query, params)


def add_answer(question_id, answer_text, author):
    """Inserts an answer to a question."""
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.product_qa`
          (qa_id, product_id, author, body, parent_question_id, parent_answer_id, created_at)
        VALUES (
          GENERATE_UUID(),
          (SELECT product_id FROM `{PROJECT_ID}.{DATASET}.product_qa`
           WHERE qa_id = @question_id LIMIT 1),
          @author, @body, @question_id, NULL, CURRENT_TIMESTAMP()
        )
    """
    params = [
        bigquery.ScalarQueryParameter("question_id", "STRING", question_id),
        bigquery.ScalarQueryParameter("author",      "STRING", author),
        bigquery.ScalarQueryParameter("body",        "STRING", answer_text),
    ]
    run_query(query, params)


def add_reply(answer_id, reply_text, author):
    """Inserts a thread reply under an answer."""
    query = f"""
        INSERT INTO `{PROJECT_ID}.{DATASET}.product_qa`
          (qa_id, product_id, author, body, parent_question_id, parent_answer_id, created_at)
        VALUES (
          GENERATE_UUID(),
          (SELECT product_id FROM `{PROJECT_ID}.{DATASET}.product_qa`
           WHERE qa_id = @answer_id LIMIT 1),
          @author, @body, NULL, @answer_id, CURRENT_TIMESTAMP()
        )
    """
    params = [
        bigquery.ScalarQueryParameter("answer_id", "STRING", answer_id),
        bigquery.ScalarQueryParameter("author",    "STRING", author),
        bigquery.ScalarQueryParameter("body",      "STRING", reply_text),
    ]
    run_query(query, params)