#!/usr/bin/env python3
"""
Detector de narrativas sobre graduadas: ¿rinden mas las monedas cuyo nombre coincide con noticias?
Para cada graduada: resultado de "entrar apenas se gradua" (salida ATM base) y
  - noticias: articulos en GDELT con el nombre de la moneda en las 12 h ANTES de graduarse
  - tema caliente: cuantas otras graduadas con la misma palabra clave hubo en las 24 h ANTES
Uso: python narrative.py --fee 0.02
"""
import argparse, json, re, sys, time, unicodedata
from datetime import datetime, timezone
import memes as M

STOP = {"coin", "token", "the", "sol", "solana", "of", "a", "on", "inu", "ai", "and", "in", "to", "is", "my", "pump", "fun"}
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"


def keywords(name):
    base = name.split(" / ")[0]
    base = unicodedata.normalize("NFKD", base).encode("ascii", "ignore").decode()
    base = re.sub(r"([a-z])([A-Z])", r"\1 \2", base)          # NvidiaCEO -> Nvidia CEO
    words = [w for w in re.findall(r"[A-Za-z0-9]+", base) if w.lower() not in STOP and len(w) >= 3]
    return words


def gdelt_count(phrase, end_ts):
    start = datetime.fromtimestamp(end_ts - 12 * 3600, timezone.utc).strftime("%Y%m%d%H%M%S")
    end = datetime.fromtimestamp(end_ts, timezone.utc).strftime("%Y%m%d%H%M%S")
    q = f'"{phrase}"' if " " in phrase else phrase
    time.sleep(5)   # GDELT pide ~1 consulta cada 5 s
    d = M.get(GDELT, {"query": q, "mode": "artlist", "format": "json", "maxrecords": 75,
                      "startdatetime": start, "enddatetime": end})
    return len((d or {}).get("articles", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mints", default="graduadas.csv")
    ap.add_argument("--fee", type=float, default=0.02)
    ap.add_argument("--budget", type=float, default=4200)
    a = ap.parse_args()
    a.stop, a.trail, a.tp, a.bounce = 0.40, 0.30, 2.0, 0.10
    t0 = time.time()
    rows = []
    for addr, name, created in M.pools_from_file(a.mints):
        if time.time() - t0 > a.budget:
            print("tiempo agotado", file=sys.stderr); break
        c = M.candles(addr, created)
        if len(c) < 2:
            continue
        r = M.simulate_cross(c, a, 0)
        kw = keywords(name)
        news = 0
        if kw:
            phrase = " ".join(kw[:3])
            try:
                news = gdelt_count(phrase, created)
                if news == 0 and len(kw) > 1:
                    news = max(gdelt_count(w, created) for w in kw[:2] if len(w) >= 4) if any(len(w) >= 4 for w in kw[:2]) else 0
            except Exception as e:
                print(f"gdelt fallo {name}: {e}", file=sys.stderr)
        rows.append({"name": name, "created": created, "kw": [w.lower() for w in kw], "news": news, "mult": r[0]})
        print(f"{len(rows)} {name} noticias={news} mult={r[0]:.2f}", file=sys.stderr)
    rows.sort(key=lambda x: x["created"])
    for i, x in enumerate(rows):   # tema caliente: misma palabra en otra graduada de las 24 h previas
        x["heat"] = sum(1 for y in rows[:i] if x["created"] - y["created"] <= 86400 and set(x["kw"]) & set(y["kw"]))
    json.dump(rows, open("narrativa.json", "w"))

    def block(title, rs):
        if not rs:
            print(f"{title:<42} n=0"); return
        n = len(rs); m = sum(x["mult"] for x in rs) / n - 1
        srt = sorted((x["mult"] for x in rs), reverse=True)
        no3 = (sum(srt[3:]) / len(srt[3:]) - 1) if n > 3 else float("nan")
        print(f"{title:<42} n={n:<4} medio {m*100:+6.1f}%  sin top3 {no3*100:+6.1f}%  ganadoras {sum(x['mult']>1 for x in rs)/n*100:4.0f}%")
    print(f"Entrar apenas se gradua, salida ATM, fee {a.fee} por lado\n")
    block("todas", rows)
    block("SIN noticias (0 articulos en 12 h)", [x for x in rows if x["news"] == 0])
    block("CON noticias (1+ articulos)", [x for x in rows if x["news"] >= 1])
    block("CON muchas noticias (10+)", [x for x in rows if x["news"] >= 10])
    block("tema caliente (1+ graduada igual en 24 h)", [x for x in rows if x["heat"] >= 1])
    block("tema NO caliente", [x for x in rows if x["heat"] == 0])
    block("noticias 1+ Y tema caliente", [x for x in rows if x["news"] >= 1 and x["heat"] >= 1])
    print("\nCon noticias, mejores:")
    for x in sorted([x for x in rows if x["news"] >= 1], key=lambda x: -x["mult"])[:12]:
        print(f"  {x['name']:<30} noticias {x['news']:>3}  tema {x['heat']}  {(x['mult']-1)*100:+.0f}%")


if __name__ == "__main__":
    main()
