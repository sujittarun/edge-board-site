"""Fetch a quote snapshot for the board, from GitHub's runners rather than anyone's laptop.

The board used to get live prices from serve_live.py over a tunnel, which meant the whole thing
went dark whenever the Mac slept - and it slept at 1% battery on 1 September, which is what the
503 actually was. The dependency was never necessary. CORS only restricts BROWSERS: a server can
read public quote endpoints freely, and none of them need a Kite token. So this runs on a schedule
in the site repo itself, and the page reads the result from its own origin.

Stdlib only, so the workflow needs no install step. Writes quotes.json in the shape the page
already expects from /api/quotes, so nothing downstream had to change to accept it.
"""
import json, os, sys, urllib.request, datetime as dt
import concurrent.futures as cf

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "Mozilla/5.0 (edge-board quote snapshot)"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}.NS?interval=1d&range=1d"


def one(sym):
    """Yahoo's per-symbol chart endpoint. The v7 batch endpoint now demands a crumb cookie and
    returns HTML to anonymous callers, so this fans out instead - 56 symbols in about 2 seconds."""
    try:
        req = urllib.request.Request(CHART.format(sym), headers=UA)
        res = json.load(urllib.request.urlopen(req, timeout=20))["chart"]["result"][0]
        m = res["meta"]
        ltp = m.get("regularMarketPrice")
        if ltp is None:
            return sym, None
        # meta carries the price and the previous close but NOT the session's open, high and low -
        # those are only in indicators.quote, and reading them from meta silently yielded nulls.
        # The page draws today's forming bar from them, so a null there is a missing candle.
        q = (res.get("indicators", {}).get("quote") or [{}])[0]
        first = lambda k: next((v for v in (q.get(k) or []) if v is not None), None)
        hi, lo = first("high"), first("low")
        r2 = lambda v: None if v is None else round(float(v), 2)
        return sym, {"ltp": round(float(ltp), 2),
                     "open": r2(first("open")),
                     "high": r2(max(hi, ltp) if hi is not None else ltp),
                     "low":  r2(min(lo, ltp) if lo is not None else ltp),
                     "prev_close": r2(m.get("chartPreviousClose") or m.get("previousClose"))}
    except Exception:
        return sym, None


def market_open(now):
    """NSE hours in IST. The runner is on UTC, so this converts rather than trusting localtime."""
    ist = now + dt.timedelta(hours=5, minutes=30)
    if ist.weekday() > 4:
        return False
    return (9, 15) <= (ist.hour, ist.minute) <= (15, 30)


def main():
    syms_path = os.path.join(HERE, "symbols.json")
    if not os.path.exists(syms_path):
        print("no symbols.json - publish_site.py writes it; nothing to fetch"); return 0
    syms = json.load(open(syms_path))
    if not syms:
        print("symbols.json is empty"); return 0

    with cf.ThreadPoolExecutor(max_workers=12) as ex:
        got = dict(ex.map(one, syms))
    quotes = {s: v for s, v in got.items() if v}
    now = dt.datetime.utcnow()

    # A snapshot that lost most of its symbols is worse than the one already on disk: it would
    # blank prices across the board and look like a crash. Keep the old file instead.
    prev_path = os.path.join(HERE, "quotes.json")
    if len(quotes) < 0.6 * len(syms) and os.path.exists(prev_path):
        print(f"only {len(quotes)}/{len(syms)} resolved - keeping the previous snapshot")
        return 0

    out = {"ok": True,
           "at": (now + dt.timedelta(hours=5, minutes=30)).isoformat(timespec="seconds"),
           "market_open": market_open(now),
           "source": "yahoo",
           "quotes": quotes}
    json.dump(out, open(prev_path, "w"), indent=1)
    print(f"{len(quotes)}/{len(syms)} quotes at {out['at']} IST  market_open={out['market_open']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
