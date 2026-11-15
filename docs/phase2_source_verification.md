# Source verification — 2026-11-15

## Observed live results

- `https://status.coinbase.com/api/v2/incidents.json`: HTTP 200. Phase 1 ingestion
  accepted 50 real records, rejected 0. The oldest observed creation timestamp
  was 2026-09-19 08:49:33.799 UTC; latest 2026-11-13 00:20:22.287 UTC.
- `https://api.coinbase.com/api/v3/brokerage/market/products/BTC-USD`: the managed
  proxy rejected CONNECT with HTTP 403. No origin response or product availability
  was observed. The enforced environment allowlist includes `status.coinbase.com`
  but not `api.coinbase.com`.
- Official candle documentation at
  `https://docs.cdp.coinbase.com/api-reference/advanced-trade-api/rest-api/public/get-public-product-candles`:
  the managed proxy rejected CONNECT with HTTP 403. This hostname is also absent
  from the environment allowlist.
- The implemented ingestion command attempted BTC-USD, ETH-USD and SOL-USD, with
  bounded retries; all three failed before receiving market observations. Failed
  runs are persisted in the real database and in the report snapshot. Requested
  default backfill: 2026-06-21 through 2026-11-15 exclusive (147 days/product).

This is a network-policy blocker, not evidence that Coinbase lacks these products
or historical data. No credentials, paid subscriptions or alternate market sources
were introduced. Source availability, date coverage and boundary behavior remain
pending until direct public endpoint access is granted in environment settings.

## Independently accessible official contract reference

Coinbase's maintained Python SDK was readable from GitHub. Its public methods and
response classes confirm the endpoint paths, unauthenticated access, parameter
names and string-valued candle fields:

- [Public methods](https://github.com/coinbase/coinbase-advanced-py/blob/338a4e34f497ff51bd6c61ae440061235dbf8fa9/coinbase/rest/public.py)
- [Product and Candle response classes](https://github.com/coinbase/coinbase-advanced-py/blob/338a4e34f497ff51bd6c61ae440061235dbf8fa9/coinbase/rest/types/product_types.py)

Observed master revision: `338a4e34f497ff51bd6c61ae440061235dbf8fa9`.
The SDK does not establish live product availability, the current maximum limit,
or historical candle coverage. The adapter requests <=300 days with limit 350;
these assumptions remain part of the live acceptance check, not a fabricated
claim of successful API verification.

## Finish live acceptance when source access is available

1. Allow `api.coinbase.com` and `docs.cdp.coinbase.com` alongside the existing hosts.
2. Inspect the official documentation and product responses; confirm ONE_DAY,
   maximum limit, string-valued numeric fields and time-boundary semantics.
3. Run `eri market-ingest` twice; confirm stable candle keys and recorded coverage.
4. Run `eri market-update`, `eri market-indicators`, `eri market-quality`, then
   `eri market-report`; inspect gaps, warmup and source attribution.
5. Interpret frequency statistics only after independent coverage review. The
   latest-50 incident feed alone does not satisfy that condition.
