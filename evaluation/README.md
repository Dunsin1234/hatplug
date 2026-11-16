# Style Coach evaluation

Measures whether the Style Coach recommends hats a shopper would like, and
whether Gemini's output is reliable enough to ship.

## How it works

- `personas.json`: 20 synthetic shoppers. Each owns hats with certain
  styles/colors (in the cart or past orders) and is built to prefer some of them.
  Personas are matched to whatever catalog you give the harness, so they work
  with both the sample catalog and your real BigQuery catalog.
- Three recommenders are compared on the same personas:
  random hats (baseline), the app's rule-based fallback, and the real Gemini Style Coach.
- Metrics: Precision@3 and Hit@3 (does a recommended hat match the persona's
  preferred style or color), plus Gemini reliability (valid JSON, exactly 3
  results, ids outside the allowed list, fallback rate, latency).

## Run it

```bash
# Offline check that the harness works (no cloud calls). Uses a fake model:
python -m evaluation.run_eval --mock

# The real evaluation: needs BigQuery + Vertex AI access (e.g. Cloud Shell
# with `gcloud auth application-default login`):
python -m evaluation.run_eval --live --repeats 5
```

Output goes to `evaluation/results/` as `live.md` (the table for your slides)
and `live.json` (raw numbers). `--mock` writes `mock.*` and is only a harness
check; **never present mock numbers as Gemini results.**

## Reading the results

- One persona (`almost_everything_owned`) leaves only 2 candidate hats on
  purpose, so "exactly 3 results" will never be 100%. That is correct behavior.
- `cold_start`, `gift_shopper` and `almost_everything_owned` have no single
  taste, so they count toward reliability but not relevance.
- Invalid ids are filtered before users see them; the invalid-id rate tells you
  how often the model would have shown a wrong or already-owned hat.
- With 17 scored personas, differences of a few points are noise. Use `--repeats`
  to average over Gemini's randomness.
