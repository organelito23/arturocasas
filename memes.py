#!/usr/bin/env python3
"""
Backtest rapido de "Sobreviviente + ATM" en memecoins graduadas de pump.fun (PumpSwap).
No necesita API keys. Tarda ~5-8 minutos (limite gratis de GeckoTerminal).

Reglas (por moneda, solo el primer trade, primeras ~16 h despues de graduarse):
  entrada: el precio cae 30-50% desde el maximo post-graduacion y rebota +10% desde el minimo
  salida : stop -40% | en 2x vende la mitad | el resto con trailing 30% desde el maximo
  costos : 5% por lado (compra y cada venta)

OJO: la lista sale de las pools con mas volumen en PumpSwap = monedas que sobrevivieron.
Eso hace el test OPTIMISTA. Si aqui sale perdiendo, la estrategia esta muerta.

Uso:  python memes.py
"""
import argparse, hashlib, json, os, sys, time
from datetime import datetime

import requests

GT = "https://api.geckoterminal.com/api/v2/networks/solana"
CACHE = "cache_memes"


def get(url, params=None):
    os.makedirs(CACHE, exist_ok=True)
    raw = url + json.dumps(params, sort_keys=True)
    key = os.path.join(CACHE, hashlib.md5(raw.encode()).hexdigest() + ".json")
    if os.path.exists(key):
        with open(key) as f:
            return json.load(f)
    for i in range(6):
        time.sleep(2.2)
        r = requests.get(url, params=params, timeout=30, headers={"accept": "application/json"})
        if r.status_code == 429:
            time.sleep(10 * (i + 1)); continue
        if r.status_code == 404:
            return None
        r.raise_for_status()
        d = r.json()
        with open(key, "w") as f:
            json.dump(d, f)
        return d
    raise RuntimeError("GeckoTerminal no responde (429)")


def pools(dex, pages):
    out = []
    for p in range(1, pages + 1):
        print(f"lista de monedas pagina {p}/{pages}", file=sys.stderr)
        d = get(f"{GT}/dexes/{dex}/pools", {"page": p, "sort": "h24_volume_usd_desc"})
        if not d:
            if p == 1:
                sys.exit(f"No encontre el dex '{dex}' en GeckoTerminal")
            break
        for x in d["data"]:
            a = x["attributes"]
            mint = x["relationships"]["base_token"]["data"]["id"].split("_", 1)[1]
            if mint.endswith("pump"):  # tokens creados en pump.fun
                created = int(datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp())
                out.append((a["address"], a["name"], created))
    return out


def pools_from_file(path, min_age_h=6):
    """Graduadas del monitor con al menos min_age_h horas. Busca pools de a 30 (rapido)."""
    now, mints = time.time(), {}
    for l in open(path):
        parts = l.strip().split(",")
        if len(parts) == 2 and parts[0].endswith("pump") and now - int(parts[1]) > min_age_h * 3600:
            mints.setdefault(parts[0], int(parts[1]))
    mints = list(mints)
    pool_of = {}
    for i in range(0, len(mints), 30):
        print(f"buscando pools {i + 1}-{min(i + 30, len(mints))}/{len(mints)}", file=sys.stderr)
        d = get(f"{GT}/tokens/multi/{','.join(mints[i:i + 30])}")
        for x in (d or {}).get("data", []):
            tp = x.get("relationships", {}).get("top_pools", {}).get("data", [])
            if tp:
                pool_of[x["attributes"]["address"]] = tp[0]["id"].split("_", 1)[1]
    pools_ = list(dict.fromkeys(pool_of.values()))
    out = []
    for i in range(0, len(pools_), 30):
        d = get(f"{GT}/pools/multi/{','.join(pools_[i:i + 30])}")
        for x in (d or {}).get("data", []):
            a = x["attributes"]
            created = int(datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp())
            out.append((a["address"], a["name"], created))
    print(f"{len(mints)} graduadas con {min_age_h}+ h, {len(out)} con pool", file=sys.stderr)
    return out


def candles(pool, created):
    d = get(f"{GT}/pools/{pool}/ohlcv/minute",
            {"aggregate": 1, "limit": 1000, "before_timestamp": created + 1000 * 60})
    rows = (d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", [])
    return sorted(rows)  # [ts, o, h, l, c, v] ascendente


def simulate(c, a):
    """Devuelve (multiplo final neto, llego_a_2x) o None si no hubo entrada."""
    hi, low_since, entry = 0.0, None, None
    for i, (ts, o, h, l, cl, v) in enumerate(c):
        if entry is None:
            hi = max(hi, h)
            if l <= hi * (1 - a.dip_min):
                if l < hi * (1 - a.dip_max):      # cayo demasiado: descartada
                    return None
                low_since = l if low_since is None else min(low_since, l)
            if low_since and cl >= low_since * (1 + a.bounce):
                if a.vol_filter:
                    vol1h = sum(r[5] for r in c[max(0, i - 59):i + 1])
                    if vol1h < cl * 1e9:          # supply pump.fun = 1.000 M
                        low_since = None; continue
                entry, peak, half, j0 = cl, cl, False, i
                break
    if entry is None:
        return None
    return exit_atm(c, j0, entry, a)


def exit_atm(c, j0, entry, a):
    """Salida ATM desde la vela j0: stop, mitad en 2x, trailing. -> (multiplo neto, llego_a_2x)"""
    fee = a.fee
    got, peak, half = 0.0, entry, False
    size = (1 - fee) / entry   # tokens comprados con 1 unidad
    for ts, o, h, l, cl, v in c[j0 + 1:]:
        stop = entry * (1 - a.stop) if not half else peak * (1 - a.trail)
        if l <= stop:                                # stop primero (conservador)
            px = min(o, stop)
            return got + size * px * (1 - fee), half
        if not half and h >= entry * 2:
            got += size / 2 * entry * 2 * (1 - fee)
            size /= 2
            half = True
        peak = max(peak, h)
    return got + size * c[-1][4] * (1 - fee), half


def simulate_cross(c, a, level):
    """Entra al cierre de la primera vela cuyo maximo cruza `level` USD de market cap
    (lo que hizo FloatLaunchpad en PUSSY entrando en $84k). None si nunca cruza."""
    if not level:
        return exit_atm(c, 0, c[0][4], a)          # entrar apenas se gradua
    for i, r in enumerate(c):
        if r[2] * 1e9 >= level:
            return exit_atm(c, i, r[4], a)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dex", default="pumpswap")
    ap.add_argument("--pages", type=int, default=10, help="20 monedas por pagina (max 10)")
    ap.add_argument("--dip-min", type=float, default=0.30)
    ap.add_argument("--dip-max", type=float, default=0.50)
    ap.add_argument("--bounce", type=float, default=0.10)
    ap.add_argument("--stop", type=float, default=0.40)
    ap.add_argument("--trail", type=float, default=0.30)
    ap.add_argument("--fee", type=float, default=0.05, help="por lado")
    ap.add_argument("--mints", help="archivo de monedas graduadas (graduadas.csv del monitor): test SIN sesgo")
    a = ap.parse_args()

    ps = pools_from_file(a.mints) if a.mints else pools(a.dex, a.pages)
    data = []
    for k, (addr, name, created) in enumerate(ps, 1):
        print(f"precios {k}/{len(ps)} {name}", file=sys.stderr)
        c = candles(addr, created)
        if len(c) >= 60:
            data.append((name, c))

    strategies = [("Sobreviviente: caida 30-50% + rebote", lambda c: simulate(c, a) if not setattr(a, "vol_filter", False) else None),
                  ("Sobreviviente + volumen 1h > mcap", lambda c: simulate(c, a) if not setattr(a, "vol_filter", True) else None),
                  ("Entrar apenas se gradua", lambda c: simulate_cross(c, a, 0)),
                  ("Entrar al cruzar $100k de mcap", lambda c: simulate_cross(c, a, 100_000)),
                  ("Entrar al cruzar $250k de mcap", lambda c: simulate_cross(c, a, 250_000)),
                  ("Entrar al cruzar $500k de mcap", lambda c: simulate_cross(c, a, 500_000))]
    for title, fn in strategies:
        res = [(n, r) for n, c in data if (r := fn(c))]
        print(f"\n=== {title} ===")
        if not res:
            print("ninguna entrada"); continue
        mult = [r[0] for _, r in res]
        hit = sum(1 for _, r in res if r[1])
        avg = sum(mult) / len(mult)
        print(f"monedas con datos: {len(data)} | entradas: {len(res)}")
        print(f"llegaron a 2x: {hit}/{len(res)} ({hit/len(res)*100:.0f}%)  [hace falta ~40%]")
        print(f"ganadoras: {sum(m > 1 for m in mult)}/{len(res)}")
        print(f"resultado medio por trade: {(avg-1)*100:+.1f}%")
        print(f"si metias $10 en cada una: ${len(res)*10} -> ${sum(mult)*10:.0f}")
        best = sorted(res, key=lambda x: -x[1][0])[:3]
        print("mejores:", ", ".join(f"{n.split(' /')[0]} {(r[0]-1)*100:+.0f}%" for n, r in best))


if __name__ == "__main__":
    main()
