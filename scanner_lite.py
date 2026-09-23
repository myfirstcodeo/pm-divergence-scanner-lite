#!/usr/bin/env python3
"""
Prediction-Market Divergence Scanner  (free lite edition)
Matches Kalshi and Polymarket contracts asking the same question and shows the price gap.
Lite edition: one-shot scan, top 10 rows, console output only.
Full edition adds unlimited rows, HTML/JSON reports, watch mode, and Telegram alerts: see README.

Python 3.9+ standard library only. No API keys.
"""
import argparse, json, re, sys, time, os, datetime as dt
import urllib.request, urllib.parse, urllib.error
from difflib import SequenceMatcher

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
UA = {"User-Agent": "pm-divergence-scanner/1.0 (personal use, read-only)"}


def get(url, retries=3):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (400, 422) or i == retries - 1:
                raise
        except Exception:
            if i == retries - 1:
                raise
        time.sleep(1.5 * (i + 1))


def fnum(x):
    try:
        if x is None or x == "":
            return None
        return float(x)
    except Exception:
        return None


def mid(b, a, last):
    if b is not None and a is not None and a > 0:
        return (b + a) / 2
    return last if last is not None else (a if a is not None else b)


# ---------------------------------------------------------------- fetch
def fetch_kalshi(max_pages=120, log=print):
    """Open Kalshi events with nested markets. Skips multivariate parlay shards (KXMVE*)."""
    out, cursor, pages = [], None, 0
    while pages < max_pages:
        q = {"limit": 200, "status": "open", "with_nested_markets": "true"}
        if cursor:
            q["cursor"] = cursor
        d = get(f"{KALSHI}/events?{urllib.parse.urlencode(q)}")
        evs = d.get("events", [])
        for ev in evs:
            if ev.get("event_ticker", "").startswith("KXMVE"):
                continue
            mkts = ev.get("markets", []) or []
            for m in mkts:
                if m.get("status") not in ("open", "active") or m.get("market_type") != "binary":
                    continue
                yb, ya = fnum(m.get("yes_bid_dollars")), fnum(m.get("yes_ask_dollars"))
                nb, na = fnum(m.get("no_bid_dollars")), fnum(m.get("no_ask_dollars"))
                last = fnum(m.get("last_price_dollars"))
                if ya is None and last is None:
                    continue
                title = ev.get("title", "")
                sub = m.get("yes_sub_title") or m.get("subtitle") or ""
                full = title if (not sub or len(mkts) == 1) else f"{title} - {sub}"
                out.append({
                    "venue": "kalshi", "id": m["ticker"], "event": title, "outcome": sub, "question": full,
                    "yes_bid": yb, "yes_ask": ya, "no_bid": nb, "no_ask": na, "yes_mid": mid(yb, ya, last),
                    "volume": fnum(m.get("volume_fp")) or 0.0, "volume_24h": fnum(m.get("volume_24h_fp")) or 0.0,
                    "liquidity": fnum(m.get("liquidity_dollars")) or 0.0, "close": m.get("close_time"),
                    "url": f"https://kalshi.com/markets/{ev.get('series_ticker', '').lower()}/{ev.get('event_ticker', '').lower()}",
                    "category": ev.get("category", ""),
                })
        cursor = d.get("cursor")
        pages += 1
        log(f"  kalshi page {pages}: {len(evs)} events, {len(out)} markets so far")
        if not cursor or not evs:
            break
    return out


def fetch_polymarket(max_pages=200, log=print):
    """Active Polymarket binary markets, highest 24h volume first. Gamma caps offset paging; we stop there."""
    out, offset, pages = [], 0, 0
    while pages < max_pages:
        q = {"limit": 100, "offset": offset, "active": "true", "closed": "false",
             "order": "volume24hr", "ascending": "false"}
        try:
            d = get(f"{GAMMA}/markets?{urllib.parse.urlencode(q)}")
        except Exception as e:
            log(f"  polymarket paging stopped at offset {offset}: {e}")
            break
        if not d:
            break
        for m in d:
            try:
                outcomes = json.loads(m.get("outcomes") or "[]")
                prices = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            except Exception:
                continue
            if len(outcomes) != 2 or outcomes[0].lower() != "yes" or len(prices) != 2:
                continue
            if not m.get("enableOrderBook", True):
                continue
            bb, ba = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
            ev = (m.get("events") or [{}])[0]
            out.append({
                "venue": "polymarket", "id": m.get("id"), "event": ev.get("title", ""),
                "outcome": m.get("groupItemTitle") or "", "question": m.get("question", ""),
                "yes_bid": bb, "yes_ask": ba,
                "no_bid": (1 - ba) if ba is not None else None, "no_ask": (1 - bb) if bb is not None else None,
                "yes_mid": mid(bb, ba, prices[0]),
                "volume": fnum(m.get("volumeNum")) or 0.0, "volume_24h": fnum(m.get("volume24hr")) or 0.0,
                "liquidity": fnum(m.get("liquidityNum")) or 0.0, "close": m.get("endDate"),
                "url": (f"https://polymarket.com/event/{ev.get('slug', '')}" if ev.get("slug")
                        else f"https://polymarket.com/market/{m.get('slug', '')}"),
                "category": "",
            })
        offset += len(d)
        pages += 1
        log(f"  polymarket page {pages}: {len(d)} markets, {len(out)} binary so far")
        if len(d) < 100:
            break
    return out


# ---------------------------------------------------------------- matching
STOP = set("will the a an be of in on at to by for or and is are does do than as vs versus with this that".split())
SYN = [("won't", "not"), ("wont", "not"), ("united states", "us"), ("u.s.", "us"), ("usa", "us"),
       ("presidential", "pres"), ("president", "pres"), ("election", "elect"), ("nominee", "nomination"),
       ("%", " percent "), ("$", " usd "),
       ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("june", "jun"), ("july", "jul"),
       ("august", "aug"), ("september", "sep"), ("sept ", "sep "), ("october", "oct"), ("november", "nov"), ("december", "dec")]
KEEP_SHORT = {"us", "uk", "eu", "un", "ai", "xi", "10y", "2y", "cpi", "gdp", "fed", "nyc", "la", "sf"}
NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# Words that change what a market is asking. Present on one side only means a different question.
PIVOT = {"closest", "margin", "first", "neither", "both", "draw", "tie", "over", "under", "above", "below",
         "least", "most", "highest", "lowest", "before", "after", "visit", "leave", "resign", "win", "lose",
         "democrat", "democrats", "republican", "republicans", "not", "impeach", "indicted", "pardon"}

# Place names. Different places on the two sides means a different question.
GEO = set("""us uk eu china taiwan russia ukraine israel iran iraq syria gaza india pakistan japan korea mexico canada
brazil venezuela argentina france germany italy spain poland turkey egypt saudi cuba greenland panama
alabama alaska arizona arkansas california colorado connecticut delaware florida georgia hawaii idaho illinois indiana
iowa kansas kentucky louisiana maine maryland massachusetts michigan minnesota mississippi missouri montana nebraska
nevada hampshire jersey york carolina dakota ohio oklahoma oregon pennsylvania rhode tennessee texas utah vermont
virginia washington wisconsin wyoming nyc chicago miami dallas houston boston philadelphia atlanta denver seattle
phoenix austin london paris berlin moscow beijing tokyo""".split())


def norm(s):
    s = s.lower()
    for k, v in SYN:
        s = s.replace(k, v)
    s = re.sub(r"(\d)\s*(?:°|º|degrees?)?\s*f", r"", s)  # 93F, 93°F, 93 degrees F -> 93
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return [t for t in s.split() if t not in STOP]


def key_tokens(toks):
    return set(t for t in toks if len(t) > 2 or t.isdigit() or t in KEEP_SHORT)


def is_year(t):
    return t.isdigit() and len(t) == 4 and 1990 <= int(t) <= 2100


def numbers(text):
    return set(NUM_RE.findall(text.lower().replace(",", "")))


def sim(a_toks, b_toks, a_raw="", b_raw=""):
    """0..1 similarity with hard vetoes for mismatched numbers, pivot words, and places."""
    A, B = key_tokens(a_toks), key_tokens(b_toks)
    if not A or not B:
        return 0.0
    na, nb = numbers(a_raw), numbers(b_raw)
    # a year mentioned on only one side (Kalshi titles carry the year, Polymarket often not) is not a conflict
    ya, yb = {t for t in na if is_year(t)}, {t for t in nb if is_year(t)}
    if ya and not yb:
        na = na - ya
    if yb and not ya:
        nb = nb - yb
    if (na or nb) and na != nb:
        return 0.0
    if (A ^ B) & PIVOT:
        return 0.0
    ga, gb = A & GEO, B & GEO
    if ga and gb and ga != gb:
        return 0.0
    shared = len(A & B)
    contain = shared / min(len(A), len(B))
    if contain < 0.6:
        return 0.0
    jacc = shared / len(A | B)
    seq = SequenceMatcher(None, " ".join(a_toks), " ".join(b_toks)).ratio()
    return 0.4 * jacc + 0.3 * contain + 0.3 * seq


def parse_dt(s):
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def match(kal, poly, threshold=0.6, max_days_apart=45, log=print):
    log(f"matching {len(kal)} kalshi x {len(poly)} polymarket markets ...")
    idx, ptoks = {}, []
    for j, p in enumerate(poly):
        t = norm(p["question"])
        ptoks.append(t)
        for tok in key_tokens(t):
            idx.setdefault(tok, set()).add(j)
    df_cap = max(20, int(0.05 * len(poly)))  # drop tokens with no discriminating power
    idx = {t: js for t, js in idx.items() if len(js) <= df_cap}
    pairs = []
    for i, k in enumerate(kal):
        kt = norm(k["question"])
        cands = {}
        for tok in key_tokens(kt):
            for j in idx.get(tok, ()):
                cands[j] = cands.get(j, 0) + 1
        best = None
        for j, c in cands.items():
            if c < 2:
                continue
            s = sim(kt, ptoks[j], k["question"], poly[j]["question"])
            if s < threshold:
                continue
            dk, dp = parse_dt(k["close"]), parse_dt(poly[j]["close"])
            if dk and dp and abs((dk - dp).days) > max_days_apart:
                continue
            if best is None or s > best[0]:
                best = (s, j)
        if best:
            pairs.append((best[0], i, best[1]))
    pairs.sort(reverse=True)
    used_k, used_p, out = set(), set(), []
    for s, i, j in pairs:
        if i in used_k or j in used_p:
            continue
        used_k.add(i)
        used_p.add(j)
        out.append(build_row(kal[i], poly[j], s))
    log(f"  matched {len(out)} pairs")
    return out


def build_row(k, p, score):
    gap = None
    if k["yes_mid"] is not None and p["yes_mid"] is not None:
        gap = k["yes_mid"] - p["yes_mid"]
    legs = []
    if k["yes_ask"] and p["no_ask"]:
        legs.append(("YES@Kalshi + NO@Polymarket", k["yes_ask"] + p["no_ask"]))
    if p["yes_ask"] and k["no_ask"]:
        legs.append(("YES@Polymarket + NO@Kalshi", p["yes_ask"] + k["no_ask"]))
    arb = None
    if legs:
        legs.sort(key=lambda x: x[1])
        arb = {"legs": legs[0][0], "cost": round(legs[0][1], 4), "edge": round(1 - legs[0][1], 4)}
    flags = []
    if gap is not None and abs(gap) >= 0.35:
        flags.append("VERIFY RULES: gap this large usually means the two contracts resolve differently")
    if score < 0.7:
        flags.append("weak match")
    return {
        "key": f"{k['id']}|{p['id']}", "score": round(score, 3), "kalshi": k, "polymarket": p,
        "gap": None if gap is None else round(gap, 4), "abs_gap": None if gap is None else round(abs(gap), 4),
        "arb": arb, "min_liquidity": min(k["volume"], p["liquidity"]), "flags": flags,
    }


# ---------------------------------------------------------------- pipeline
def run_scan(a, log, raw=None):
    if raw is None:
        log("fetching kalshi ...")
        kal = fetch_kalshi(log=log)
        log("fetching polymarket ...")
        poly = fetch_polymarket(log=log)
        raw = {"kalshi": kal, "polymarket": poly, "fetched": dt.datetime.now(dt.timezone.utc).isoformat()}
        if a.cache:
            json.dump(raw, open(a.cache, "w", encoding="utf-8"))
    kal, poly = raw["kalshi"], raw["polymarket"]
    # Kalshi nested markets report liquidity 0; gate on lifetime volume and a two-sided book instead
    kal = [k for k in kal if k["volume"] >= a.min_liq and k["yes_mid"] is not None
           and k["yes_bid"] is not None and k["yes_ask"] is not None and 0 < k["yes_ask"] < 1]
    poly = [p for p in poly if p["liquidity"] >= a.min_liq and p["yes_bid"] is not None and p["yes_ask"] is not None]
    log(f"after liquidity filter (>= ${a.min_liq:,.0f}): {len(kal)} kalshi, {len(poly)} polymarket")
    rows = match(kal, poly, threshold=a.threshold, log=log)
    rows = [r for r in rows if r["abs_gap"] is not None and r["abs_gap"] >= a.min_gap and r["min_liquidity"] >= a.min_liq]
    rows.sort(key=lambda r: (-(r["arb"]["edge"] if r["arb"] else -1), -r["abs_gap"]))
    meta = {"kalshi_n": len(kal), "polymarket_n": len(poly), "matched": len(rows),
            "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    return rows, meta


# ---------------------------------------------------------------- output
def fmt_table(rows, n=30):
    lines = []
    hdr = f"{'gap':>6} {'K yes':>6} {'P yes':>6} {'arb edge':>8} {'liq$':>9}  question"
    lines.append(hdr)
    lines.append("-" * len(hdr))
    for r in rows[:n]:
        k, p = r["kalshi"], r["polymarket"]
        edge = f"{r['arb']['edge']:+.3f}" if r["arb"] else "n/a"
        flag = "  !" if r["flags"] else ""
        lines.append(f"{r['gap']:+.3f} {k['yes_mid']:6.3f} {p['yes_mid']:6.3f} {edge:>8} {r['min_liquidity']:9.0f}  {k['question'][:70]}{flag}")
    return "\n".join(lines)


def fmt_alert(r):
    k, p = r["kalshi"], r["polymarket"]
    edge = f"{r['arb']['edge'] * 100:+.1f}c via {r['arb']['legs']}" if r["arb"] else "n/a"
    flags = ("\n" + "; ".join(r["flags"])) if r["flags"] else ""
    return (f"{k['question']}\nKalshi YES {k['yes_mid'] * 100:.1f}c | Polymarket YES {p['yes_mid'] * 100:.1f}c | gap {r['gap'] * 100:+.1f}c\n"
            f"arb edge: {edge}\n{k['url']}\n{p['url']}{flags}")


def to_html(rows, meta):
    def esc(s):
        return (s or "").replace("&", "&amp;").replace("<", "&lt;")
    trs = []
    for r in rows:
        k, p = r["kalshi"], r["polymarket"]
        arb = r["arb"]
        arbtxt = f"{arb['edge'] * 100:+.1f}c ({arb['legs']})" if arb else "n/a"
        cls = "arb" if arb and arb["edge"] > 0 else ""
        flags = f"<br><b style='color:#b00'>{esc('; '.join(r['flags']))}</b>" if r["flags"] else ""
        trs.append(
            f"<tr class='{cls}'><td>{r['gap'] * 100:+.1f}c</td><td>{k['yes_mid'] * 100:.1f}c</td>"
            f"<td>{p['yes_mid'] * 100:.1f}c</td><td>{arbtxt}</td><td>${r['min_liquidity']:,.0f}</td>"
            f"<td><a href='{esc(k['url'])}'>{esc(k['question'])}</a><br><small><a href='{esc(p['url'])}'>"
            f"{esc(p['question'])}</a> &middot; match {r['score']:.2f}</small>{flags}</td></tr>")
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Kalshi vs Polymarket Divergence</title>
<style>body{{font:14px system-ui;margin:24px;max-width:1100px}}table{{border-collapse:collapse;width:100%}}
td,th{{padding:6px 8px;border-bottom:1px solid #ddd;vertical-align:top}}th{{text-align:left;background:#f4f4f4}}
tr.arb td{{background:#eefbe9}}small{{color:#666}}</style></head><body>
<h1>Kalshi vs Polymarket divergence</h1>
<p>Scanned {meta['kalshi_n']:,} Kalshi and {meta['polymarket_n']:,} Polymarket binary markets, matched {meta['matched']:,} pairs
above the filters. Generated {meta['generated']}. Gap = Kalshi YES mid minus Polymarket YES mid.
Arb edge = 1 minus (YES ask on one venue plus NO ask on the other); positive means a locked-in gross edge before fees.</p>
<table><tr><th>Gap</th><th>Kalshi YES</th><th>Poly YES</th><th>Arb edge</th><th>Min liq</th><th>Market</th></tr>{''.join(trs)}</table>
<p><small>Not financial advice. Prices are snapshots; fees, slippage, and resolution-rule differences between venues can erase any gap.
Always read both rule sets before acting.</small></p></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-gap", type=float, default=0.03)
    ap.add_argument("--min-liq", type=float, default=500.0)
    ap.add_argument("--threshold", type=float, default=0.6)
    a = ap.parse_args()
    a.cache = None

    def log(s):
        print(s, file=sys.stderr)

    rows, meta = run_scan(a, log)
    print(fmt_table(rows, 10))
    if len(rows) > 10:
        print("")
        print(f"... {len(rows) - 10} more pairs in the full edition (HTML/JSON reports, watch mode, Telegram alerts).")


if __name__ == "__main__":
    main()
