#!/usr/bin/env python3
"""
Monitor en vivo de lanzamientos de pump.fun, en PAPEL (no compra nada).

Ve cada moneda nueva desde su primer segundo, sigue sus trades N minutos y
anota que habria pasado si entrabas al minuto 1 (o a los 30 s, 2 min...).
Incluye TODAS las monedas, tambien las que mueren: esa es la gracia.
Cada 10 min imprime el resumen. Ctrl+C para parar; todo queda en monitor.csv.

Uso:
  pip install websockets
  python monitor.py
  python monitor.py --track 15 --entry 60 --fee 0.03
"""
import argparse, asyncio, csv, json, os, sys, time

import websockets

WS = "wss://pumpportal.fun/api/data"


class Coin:
    def __init__(self, m, now):
        self.mint, self.name, self.t0 = m["mint"], m.get("symbol", "?"), now
        self.dev = m.get("traderPublicKey")
        self.pts = [(0.0, mcap(m))]          # (segundos desde lanzamiento, mcap en SOL)
        self.buyers = set()
        self.dev_sold = False
        self.crossed = None                  # segundo en que cruzo --cross SOL de mcap

    def add(self, m, now):
        t = now - self.t0
        self.pts.append((t, mcap(m)))
        if self.crossed is None and self.pts[-1][1] >= A.cross:
            self.crossed = t
        if m.get("txType") == "buy" and t <= 60:
            self.buyers.add(m.get("traderPublicKey"))
        if m.get("txType") == "sell" and m.get("traderPublicKey") == self.dev and t <= 60:
            self.dev_sold = True


def mcap(m):
    if m.get("marketCapSol"):
        return float(m["marketCapSol"])
    vs, vt = m.get("vSolInBondingCurve"), m.get("vTokensInBondingCurve")
    return float(vs) / float(vt) * 1e9 if vs and vt else 0.0


def evaluate(c, a, entry_t, horizon):
    """Resultado de entrar en t=entry_t con reglas ATM, mirando hasta horizon segundos."""
    after = [(t, p) for t, p in c.pts if entry_t <= t <= horizon and p > 0]
    if not after:
        return None
    entry = after[0][1]
    peak, half, got, size = entry, False, 0.0, (1 - a.fee) / entry
    for t, p in after[1:]:
        stop = entry * (1 - a.stop) if not half else peak * (1 - a.trail)
        if p <= stop:
            return {"mult": got + size * p * (1 - a.fee), "max": max(q for _, q in after) / entry, "half": half}
        if not half and p >= 2 * entry:
            got += size / 2 * p * (1 - a.fee); size /= 2; half = True
        peak = max(peak, p)
    last = after[-1][1]
    return {"mult": got + size * last * (1 - a.fee), "max": max(q for _, q in after) / entry, "half": half}


def summary(rows, cross_rows):
    def block(title, rs):
        if not rs:
            print(f"  {title}: sin datos"); return
        n = len(rs)
        pct = lambda f: f"{sum(1 for r in rs if f(r)) / n * 100:4.0f}%"
        avg = sum(r["mult"] for r in rs) / n
        print(f"  {title:<34} n={n:<5} toco 2x {pct(lambda r: r['max'] >= 2)} | 10x {pct(lambda r: r['max'] >= 10)}"
              f" | ganadoras {pct(lambda r: r['mult'] > 1)} | medio por trade {(avg - 1) * 100:+6.1f}%")
    print(f"\n===== {time.strftime('%H:%M')} | monedas cerradas: {len(rows)} =====")
    block("todas (entrando al minuto)", rows)
    block(">=10 compradores en el 1er minuto", [r for r in rows if r["buyers"] >= 10])
    block(">=10 compradores y dev no vendio", [r for r in rows if r["buyers"] >= 10 and not r["dev_sold"]])
    block(">=25 compradores y dev no vendio", [r for r in rows if r["buyers"] >= 25 and not r["dev_sold"]])
    block(f"entrando al cruzar {A.cross:.0f} SOL de mcap", cross_rows)
    sys.stdout.flush()


async def run(a):
    coins, rows, cross_rows = {}, [], []
    new_file = not os.path.exists("monitor.csv")
    f = open("monitor.csv", "a", newline="")
    w = csv.writer(f)
    if new_file:
        w.writerow(["mint", "name", "lanzada", "buyers_1min", "dev_vendio", "mult_neto", "max_desde_entrada", "toco_2x"])
    last_print = time.time()
    while True:
        try:
            async with websockets.connect(WS, ping_interval=20, max_size=None) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                await ws.send(json.dumps({"method": "subscribeMigration"}))
                if coins:
                    await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": list(coins)}))
                print("conectado, mirando lanzamientos...", file=sys.stderr)
                async for raw in ws:
                    now = time.time()
                    m = json.loads(raw)
                    mint = m.get("mint")
                    if mint and ("migrat" in str(m.get("txType", "")).lower() or m.get("pool") == "pump-amm" and mint not in coins and m.get("txType") not in ("buy", "sell", "create")):
                        with open("graduadas.csv", "a") as g:
                            g.write(f"{mint},{int(now)}\n")
                    if m.get("txType") == "create" and mint:
                        coins[mint] = Coin(m, now)
                        await ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": [mint]}))
                    elif mint in coins and m.get("txType") in ("buy", "sell"):
                        coins[mint].add(m, now)
                    # cerrar las que ya cumplieron el tiempo
                    done = [k for k, c in coins.items() if now - c.t0 >= a.track * 60
                            and (c.crossed is None or now - c.t0 >= c.crossed + a.track_cross * 60)]
                    if done:
                        await ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": done}))
                    for k in done:
                        c = coins.pop(k)
                        if c.crossed is not None:
                            rc = evaluate(c, a, c.crossed, c.crossed + a.track_cross * 60)
                            if rc:
                                cross_rows.append(rc)
                                print(f"  [cruce] {c.name}: max {rc['max']:.1f}x desde la entrada, neto {(rc['mult']-1)*100:+.0f}%", file=sys.stderr)
                        r = evaluate(c, a, a.entry, a.track * 60)
                        if r is None:   # nadie opero despues del minuto = muerta: cuenta como -100% si entrabas
                            r = {"mult": 0.0, "max": 0.0, "half": False} if len(c.pts) > 1 else None
                        if r is None:
                            continue
                        r.update(buyers=len(c.buyers), dev_sold=c.dev_sold)
                        rows.append(r)
                        w.writerow([c.mint, c.name, time.strftime("%Y-%m-%d %H:%M", time.localtime(c.t0)),
                                    r["buyers"], int(r["dev_sold"]), round(r["mult"], 4), round(r["max"], 3), int(r["half"])])
                        f.flush()
                    if now - last_print >= a.every * 60:
                        print(f"(siguiendo {len(coins)} monedas abiertas)", file=sys.stderr)
                        summary(rows, cross_rows)
                        last_print = now
        except Exception as e:  # 502, cortes, lo que sea: reconectar
            print(f"conexion perdida ({e}), reconectando en 5 s...", file=sys.stderr)
            await asyncio.sleep(5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", type=float, default=15, help="minutos que se sigue cada moneda")
    ap.add_argument("--entry", type=float, default=60, help="segundos tras el lanzamiento para entrar")
    ap.add_argument("--stop", type=float, default=0.40)
    ap.add_argument("--trail", type=float, default=0.30)
    ap.add_argument("--fee", type=float, default=0.03, help="por lado (pump.fun 1%% + prioridad + slippage)")
    ap.add_argument("--cross", type=float, default=400, help="mcap en SOL para la entrada por cruce")
    ap.add_argument("--track-cross", type=float, default=120, help="minutos que se sigue tras el cruce")
    ap.add_argument("--every", type=float, default=10, help="minutos entre resumenes")
    a = ap.parse_args()
    global A
    A = a
    try:
        asyncio.run(run(a))
    except KeyboardInterrupt:
        print("\nparado. Todo quedo en monitor.csv")


if __name__ == "__main__":
    main()
