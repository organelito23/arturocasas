#!/usr/bin/env python3
"""Polymarket: ¿los precios estan bien calibrados? Mercados binarios ya resueltos.
Para cada mercado: precio de SI 24 h y 6 h antes del cierre, y como resolvio.
Estrategia 'favorito al final': comprar el lado que vale >= X a 24 h del cierre y esperar la resolucion."""
import json, sys, time, requests

G = "https://gamma-api.polymarket.com/markets"
C = "https://clob.polymarket.com/prices-history"
S = requests.Session(); S.headers["accept"] = "application/json"


def get(url, params):
    for i in range(5):
        try:
            r = S.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(5 * (i + 1)); continue
            r.raise_for_status(); return r.json()
        except Exception as e:
            last = e; time.sleep(2 * (i + 1))
    print("fallo", url, params, last, file=sys.stderr); return None


def iso(s):
    from datetime import datetime
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


markets, seen = [], set()
import datetime as _dt
pages = []
mo = _dt.date(2024, 1, 1)
while mo < _dt.date(2026, 11, 1):
    nxt = (mo.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    for off in range(0, 2500, 500):
        pages.append((mo.isoformat(), nxt.isoformat(), off))
    mo = nxt
for a_, b_, off in pages:
    d = get(G, {"closed": "true", "limit": 500, "offset": off, "end_date_min": a_, "end_date_max": b_, "volume_num_min": 1000})
    if not d:
        continue
    for m in d:
        if m.get("id") in seen:
            continue
        seen.add(m.get("id"))
        try:
            outs = json.loads(m.get("outcomes") or "[]"); px = [float(x) for x in json.loads(m.get("outcomePrices") or "[]")]
            toks = json.loads(m.get("clobTokenIds") or "[]")
        except Exception:
            continue
        if len(outs) != 2 or len(toks) != 2 or sorted(px) != [0.0, 1.0]:
            continue                     # solo binarios resueltos limpio
        end = m.get("closedTime") or m.get("endDate")
        if not end:
            continue
        markets.append({"q": m.get("question"), "tok": toks[0], "yes_won": px[0] == 1.0, "end": iso(end.replace(" ", "T").replace("+00", "+00:00") if "+00" in end and "+00:00" not in end else end), "vol": m.get("volumeNum")})
print(f"mercados binarios resueltos: {len(markets)}", file=sys.stderr)

rows = []
import random as _r
_r.seed(1); _r.shuffle(markets); markets = markets[:6000]
for k, m in enumerate(markets):
    h = get(C, {"market": m["tok"], "startTs": m["end"] - 8 * 86400, "endTs": m["end"], "fidelity": 60})
    hist = (h or {}).get("history", [])
    if not hist:
        continue
    def at(ts):
        prev = None
        for p in hist:
            if p["t"] > ts:
                break
            prev = p["p"]
        return prev
    rows.append({"q": m["q"], "yes_won": m["yes_won"], "vol": m["vol"],
                 "p24": at(m["end"] - 86400), "p6": at(m["end"] - 6 * 3600), "p72": at(m["end"] - 3 * 86400)})
    if k % 200 == 0:
        print(f"historiales {k}/{len(markets)}", file=sys.stderr)
    time.sleep(0.1)
json.dump(rows, open("poly_rows.json", "w"))
print(f"con historial: {len(rows)}", file=sys.stderr)
