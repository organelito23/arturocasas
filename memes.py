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


def pools_from_file(path):
    out, now = [], time.time()
    mints = [l.split(",")[0].strip() for l in open(path) if l.strip()]
    for k, mint in enumerate(dict.fromkeys(mints), 1):
        print(f"buscando pool {k}/{len(mints)}", file=sys.stderr)
        d = get(f"{GT}/tokens/{mint}/pools", {"page": 1})
        for x in (d or {}).get("data", []):
            a = x["attributes"]
            created = int(datetime.fromisoformat(a["pool_created_at"].replace("Z", "+00:00")).timestamp())
            if now - created > 6 * 3600:   # al menos 6 h de historia
                out.append((a["address"], a["name"], created))
            break
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
    fee = a.fee
    got = 0.0              # lo que vuelve por cada 1 unidad invertida
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

    for vf in (False, True):
        a.vol_filter = vf
        res = [(n, r) for n, c in data if (r := simulate(c, a))]
        print(f"\n=== {'CON' if vf else 'SIN'} filtro volumen 1h > market cap ===")
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
