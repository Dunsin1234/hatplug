#############################################################################
# evaluation/run_eval.py
#
# Evaluates the Style Coach on synthetic shopper personas.
#
# For each persona we build a cart and order history from the catalog, then
# compare three recommenders on "does it suggest hats the persona likes?":
#   - random      : 3 random unowned, in-stock hats (baseline)
#   - rule_based  : the app's no-AI fallback
#   - gemini      : the real Style Coach prompt + JSON schema
# and measure Gemini's reliability (valid JSON, hallucinated ids, latency).
#
# Usage (from the repo root):
#   python -m evaluation.run_eval --mock                 # offline, no cloud calls
#   python -m evaluation.run_eval --live                 # real catalog + Gemini
#   python -m evaluation.run_eval --live --repeats 5
#############################################################################
import argparse
import json
import os
import random
import statistics
import time
from datetime import date

import data_fetcher

HERE = os.path.dirname(os.path.abspath(__file__))
K = data_fetcher.NUM_RECOMMENDATIONS


# --------------------------------------------------------------------------
# Building a persona's history from whatever catalog we are given
# --------------------------------------------------------------------------
def _matches(product, spec):
    for key in ("style", "color"):
        if key in spec and str(product.get(key, "")).lower() != spec[key].lower():
            return False
    return True


def materialize(persona, catalog):
    """Returns (cart_items, orders). Raises ValueError if the catalog has no
    product matching one of the persona's traits."""
    cart, orders, used = [], [], set()
    if "owns_all_but" in persona:
        owned = catalog[: max(0, len(catalog) - persona["owns_all_but"])]
        return [], [{"product_id": p["product_id"], "quantity": 1} for p in owned]

    for spec in persona["owns"]:
        pick = next((p for p in catalog
                     if p["product_id"] not in used and _matches(p, spec)), None)
        if pick is None:
            raise ValueError(f"no catalog product matches {spec}")
        used.add(pick["product_id"])
        item = {"product_id": pick["product_id"], "name": pick["name"],
                "style": pick.get("style", ""), "color": pick.get("color", ""),
                "quantity": 1}
        (cart if spec.get("where") == "cart" else orders).append(item)
    return cart, orders


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------
def is_relevant(product, likes):
    return (str(product.get("style", "")).lower() in {s.lower() for s in likes["styles"]}
            or str(product.get("color", "")).lower() in {c.lower() for c in likes["colors"]})


def score(recs, by_id, likes):
    """(precision, hit) for a list of recommendations."""
    if not recs:
        return 0.0, 0.0
    flags = [is_relevant(by_id[r["product_id"]], likes) for r in recs if r["product_id"] in by_id]
    if not flags:
        return 0.0, 0.0
    return sum(flags) / len(flags), float(any(flags))


def raw_output_checks(raw_text, candidate_ids):
    """Reliability facts about one raw Gemini response."""
    try:
        data = json.loads(raw_text)
        items = data["recommendations"]
        valid_json = isinstance(items, list) and all(
            isinstance(i, dict) and {"product_id", "style_note", "reason"} <= set(i) for i in items)
    except (TypeError, ValueError, KeyError):
        return {"valid_json": False, "count_ok": False, "invalid_id_rate": 1.0}
    ids = [str(i.get("product_id")) for i in items if isinstance(i, dict)]
    bad = [i for i in ids if i not in candidate_ids]
    return {
        "valid_json": bool(valid_json),
        "count_ok": len(items) == K,
        "invalid_id_rate": (len(bad) / len(ids)) if ids else 1.0,
    }


def _mean(values):
    return round(statistics.mean(values), 3) if values else None


# --------------------------------------------------------------------------
# The evaluation
# --------------------------------------------------------------------------
def evaluate(catalog, personas, model_fn, repeats=3, random_trials=50, seed=7):
    rng = random.Random(seed)
    methods = {m: {"precision": [], "hit": []} for m in ("random", "rule_based", "gemini")}
    rel = {"valid_json": [], "count_ok": [], "invalid_id_rate": [], "latency_s": [], "fell_back": []}
    skipped, per_persona = [], []

    for persona in personas:
        try:
            cart, orders = materialize(persona, catalog)
        except ValueError as e:
            skipped.append({"persona": persona["id"], "reason": str(e)})
            continue
        owned = data_fetcher._owned_ids(cart, orders)
        candidates = data_fetcher.build_candidate_catalog(catalog, owned)
        if not candidates:
            skipped.append({"persona": persona["id"], "reason": "no candidates left"})
            continue
        by_id = {c["product_id"]: c for c in candidates}
        cand_ids = set(by_id)
        prompt = data_fetcher.build_recommendation_prompt(cart, orders, candidates)
        likes = persona["likes"]
        scored = bool(likes["styles"] or likes["colors"])

        # reliability: always measured, including cold start and tiny pools
        gem_scores = []
        for _ in range(repeats):
            start = time.time()
            try:
                raw = model_fn(prompt)
            except Exception:
                raw = ""
            rel["latency_s"].append(time.time() - start)
            checks = raw_output_checks(raw, cand_ids)
            for k, v in checks.items():
                rel[k].append(float(v))
            recs = data_fetcher.parse_recommendations(raw, candidates)
            rel["fell_back"].append(float(not recs))
            if not recs:  # same behaviour as the app
                recs = data_fetcher.fallback_recommendations(candidates, cart, orders, catalog)
            gem_scores.append(score(recs, by_id, likes))

        row = {"persona": persona["id"], "candidates": len(candidates), "scored": scored}
        if scored:
            rb = score(data_fetcher.fallback_recommendations(candidates, cart, orders, catalog), by_id, likes)
            rnd = [score([{"product_id": c["product_id"]} for c in rng.sample(candidates, min(K, len(candidates)))],
                         by_id, likes) for _ in range(random_trials)]
            for name, pairs in (("random", rnd), ("rule_based", [rb]), ("gemini", gem_scores)):
                p, h = _mean([x[0] for x in pairs]), _mean([x[1] for x in pairs])
                methods[name]["precision"].append(p)
                methods[name]["hit"].append(h)
                row[name] = {"precision": p, "hit": h}
        per_persona.append(row)

    return {
        "n_personas_scored": len(methods["gemini"]["precision"]),
        "n_personas_total": len(personas),
        "skipped": skipped,
        "repeats": repeats,
        "relevance": {m: {"precision_at_3": _mean(v["precision"]), "hit_at_3": _mean(v["hit"])}
                      for m, v in methods.items()},
        "gemini_reliability": {
            "valid_json_rate": _mean(rel["valid_json"]),
            "exactly_3_rate": _mean(rel["count_ok"]),
            "invalid_id_rate": _mean(rel["invalid_id_rate"]),
            "fallback_rate": _mean(rel["fell_back"]),
            "mean_latency_s": _mean(rel["latency_s"]),
            "calls": len(rel["valid_json"]),
        },
        "per_persona": per_persona,
    }


def render_markdown(r, source):
    rel, g = r["relevance"], r["gemini_reliability"]
    pct = lambda x: "n/a" if x is None else f"{x * 100:.0f}%"
    lines = [
        "# Style Coach evaluation",
        "",
        f"Run on {date.today().isoformat()} · catalog: {source} · "
        f"{r['n_personas_scored']} scored personas of {r['n_personas_total']} "
        f"· {r['repeats']} Gemini call(s) per persona",
        "",
        "## Does it recommend hats the shopper likes?",
        "",
        "| Method | Precision@3 | Hit@3 |",
        "|---|---|---|",
    ]
    for name, label in (("random", "Random baseline"), ("rule_based", "Rule-based fallback"), ("gemini", "Gemini Style Coach")):
        lines.append(f"| {label} | {pct(rel[name]['precision_at_3'])} | {pct(rel[name]['hit_at_3'])} |")
    lines += [
        "",
        "Precision@3 is the share of recommended hats that match a style or color "
        "the persona was built to prefer. Hit@3 is the share of personas with at least one match.",
        "",
        "## Is Gemini's output reliable?",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Calls | {g['calls']} |",
        f"| Valid JSON in the expected shape | {pct(g['valid_json_rate'])} |",
        f"| Exactly 3 recommendations | {pct(g['exactly_3_rate'])} |",
        f"| Product ids not in the allowed list (hallucinated or already owned) | {pct(g['invalid_id_rate'])} |",
        f"| Calls that fell back to the rule-based picker | {pct(g['fallback_rate'])} |",
        f"| Mean latency | {g['mean_latency_s']} s |",
        "",
        "Invalid ids are removed before they reach the user, so none are shown.",
        "",
        "## Limits of this evaluation",
        "",
        "- Personas are synthetic and tastes are defined by simple style/color traits, not real shoppers.",
        "- Relevance is a heuristic. It does not judge the quality of the written style note or reason.",
        f"- Small sample ({r['n_personas_scored']} personas); treat differences of a few points as noise.",
    ]
    if r["skipped"]:
        lines.append(f"- Skipped {len(r['skipped'])} persona(s) the catalog could not satisfy: "
                     + ", ".join(s["persona"] for s in r["skipped"]) + ".")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------
def _mock_model():
    """Offline stand-in for Gemini: valid JSON using only the ids in the
    prompt, but it simply takes the first three candidates, so it knows nothing
    about the persona's taste. Used to test the harness, not to measure Gemini."""
    def model(prompt):
        marker = "from this list:"
        line = prompt[prompt.index(marker) + len(marker):].strip().splitlines()[0]
        picks = json.loads(line)[:K]
        return json.dumps({"recommendations": [
            {"product_id": c["product_id"], "style_note": "mock", "reason": "mock"} for c in picks]})
    return model


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="real BigQuery catalog + real Gemini")
    mode.add_argument("--mock", action="store_true", help="synthetic catalog + offline mock model")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    args = ap.parse_args()

    personas = json.load(open(os.path.join(HERE, "personas.json")))
    if args.live:
        catalog = data_fetcher.get_products()
        model_fn, source = data_fetcher.call_gemini, "live BigQuery"
    else:
        catalog = json.load(open(os.path.join(HERE, "sample_catalog.json")))
        model_fn, source = _mock_model(), "synthetic sample + mock model (not a Gemini result)"

    results = evaluate(catalog, personas, model_fn, repeats=args.repeats)
    os.makedirs(args.out, exist_ok=True)
    stem = "live" if args.live else "mock"
    json.dump(results, open(os.path.join(args.out, f"{stem}.json"), "w"), indent=2)
    md = render_markdown(results, source)
    open(os.path.join(args.out, f"{stem}.md"), "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
