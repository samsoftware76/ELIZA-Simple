"""
Approximate exchange rates for the DISPLAY-ONLY currency conversion feature
(see filters.approx_display and User.display_currency in models/models.py).

Nothing here ever touches stored amounts, PesaPal charging, or any real
money figure - a rate is only used to render an approximate "(~ USD 135.20)"
next to the real amount on staff-side pages, and a missing rate simply hides
that text.

Source: the free fawazahmed0 currency API served from jsDelivr, chosen
deliberately over Frankfurter because the ECB list behind Frankfurter lacks
East African currencies (UGX/KES/TZS/RWF) while this one covers 200+ codes.
Primary host is jsDelivr's CDN; the project's documented fallback host on
Cloudflare Pages is tried second.

Caching: a module-level in-process dict keyed by (lowercase) base currency,
6-hour TTL for a successful fetch. Honest caveat: on serverless (Vercel) the
process is ephemeral, so this cache resets on every cold start - acceptable,
the worst case is one extra ~4s fetch per cold start, and a fetch failure
just means no approx text (never an error, never a fabricated number).
Failures are cached too, with a short ~5 minute TTL, so a provider outage
costs ONE timed-out request per 5 minutes instead of adding 4s of latency
to every page render.

Public API: get_rate(from_cur, to_cur) -> float or None. NEVER raises - any
failure (network, bad JSON, unknown currency code, bad input types) logs a
warning and returns None.
"""
import logging
import threading
import time

import requests

logger = logging.getLogger(__name__)

# {base} is the lowercase currency code, e.g. .../currencies/ugx.json ->
# {"date": "2026-09-02", "ugx": {"usd": 0.00027, "kes": ..., ...}}
RATE_HOSTS = [
    'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base}.json',
    'https://latest.currency-api.pages.dev/v1/currencies/{base}.json',
]

REQUEST_TIMEOUT = 4          # seconds, per host
SUCCESS_TTL = 6 * 60 * 60    # 6 hours - display-only approximations don't need freshness
FAILURE_TTL = 5 * 60         # 5 minutes - retry an outage soon, but not on every render

# base (lowercase) -> (expires_at_monotonic, rates_dict_or_None)
# rates_dict is the {"usd": 0.00027, ...} table; None records a cached failure.
_cache = {}
_cache_lock = threading.Lock()


def _fetch_rate_table(base):
    """Fetch the full rates table for one lowercase base code, or None.

    Tries the primary CDN host then the documented fallback. Never raises.
    """
    for host in RATE_HOSTS:
        url = host.format(base=base)
        try:
            resp = requests.get(url, timeout=REQUEST_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("exchange_rates: HTTP %s from %s", resp.status_code, url)
                continue
            data = resp.json()
            table = data.get(base)
            if isinstance(table, dict) and table:
                return table
            logger.warning("exchange_rates: unexpected response shape from %s", url)
        except Exception as e:
            logger.warning("exchange_rates: fetch failed for %s: %s", url, e)
    return None


def _get_rate_table(base):
    """Cached rates table for a lowercase base code, or None. Never raises."""
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(base)
        if cached is not None and cached[0] > now:
            return cached[1]
    # Fetch outside the lock - a 4s network call must not serialize every
    # other request's (cache-hitting) lookups behind it. Two concurrent
    # misses may both fetch; harmless, last write wins with identical data.
    table = _fetch_rate_table(base)
    ttl = SUCCESS_TTL if table is not None else FAILURE_TTL
    with _cache_lock:
        _cache[base] = (time.monotonic() + ttl, table)
    return table


def get_rate(from_cur, to_cur):
    """Approximate rate to multiply an amount in from_cur by to get to_cur.

    Returns a float, or None whenever a real rate isn't available for any
    reason (bad/missing codes, network failure, unknown currency). NEVER
    raises - callers treat None as "show no conversion".
    """
    try:
        if not from_cur or not to_cur:
            return None
        base = str(from_cur).strip().lower()
        target = str(to_cur).strip().lower()
        if not base or not target:
            return None
        if base == target:
            return 1.0
        table = _get_rate_table(base)
        if not table:
            return None
        rate = table.get(target)
        if rate is None:
            logger.warning("exchange_rates: no %s rate in %s table", target, base)
            return None
        rate = float(rate)
        if rate <= 0:
            return None
        return rate
    except Exception as e:
        logger.warning("exchange_rates: get_rate(%r, %r) failed: %s", from_cur, to_cur, e)
        return None
