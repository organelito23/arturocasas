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
import argparse, asyncio, base64, csv, hashlib, json, os, struct, sys, time

import websockets

WS = "wss://pumpportal.fun/api/data"
HELIUS = "wss://mainnet.helius-rpc.com/?api-key="
TRADE_DISC = hashlib.sha256(b"event:TradeEvent").digest()[:8]
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def decode_trade(data_b64):
    """TradeEvent de pump.fun (Anchor) -> dict o None."""
    try:
        d = base64.b64decode(data_b64)
    except Exception:
        return None
    if len(d) < 113 or d[:8] != TRADE_DISC:
        return None
    sol, tok = struct.unpack_from("<QQ", d, 40)
    is_buy = d[56] == 1
    vsol, vtok = struct.unpack_from("<QQ", d, 97)
    return {"mint": b58(d[8:40]), "user": b58(d[57:89]), "is_buy": is_buy, "sol": sol / 1e9,
            "mcap": vsol / vtok * 1e6 if vtok else 0.0}


class Coin:
    def __init__(self, m, now):
        self.mint, self.name, self.t0 = m["mint"], m.get("symbol", "?"), now
        self.dev = m.get("traderPublicKey")
        self.curve = m.get("bondingCurveKey")
        self.pts = [(0.0, mcap(m))]          # (segundos desde lanzamiento, mcap en SOL)
        self.buyers = set()
        self.dev_sold = False
        self.crossed = None                  # segundo en que cruzo --cross SOL de mcap
        self.checked = False                 # ya se miro si vale la pena seguirla
        self.light = False                   # pocos compradores: se dejo de seguir (ahorra creditos)

    def add(self, m, now):
        t = now - self.t0
        self.pts.append((t, mcap(m)))
        if self.crossed is None and self.pts[-1][1] >= A.cross:
            self.crossed = t
        if m.get("txType") == "buy" and t <= 60:
            self.buyers.add(m.get("traderPublicKey"))
        if m.get("txType") == "sell" and m.get("traderPublicKey") == self.dev and t <= 60:
            self.dev_sold = True


    def add_trade(self, ev, now):
        self.add({"marketCapSol": ev["mcap"], "txType": "buy" if ev["is_buy"] else "sell",
                  "traderPublicKey": ev["user"]}, now)


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
    block(f"todas con >={A.min_buyers} compradores (min 1)", rows)
    block(">=10 compradores en el 1er minuto", [r for r in rows if r["buyers"] >= 10])
    block(">=10 compradores y dev no vendio", [r for r in rows if r["buyers"] >= 10 and not r["dev_sold"]])
    block(">=25 compradores y dev no vendio", [r for r in rows if r["buyers"] >= 25 and not r["dev_sold"]])
    block(f"entrando al cruzar {A.cross:.0f} SOL de mcap", cross_rows)
    sys.stdout.flush()


async def helius_trades(key, coins, hq):
    """Trades de cada moneda nueva via logsSubscribe a su bonding curve (Helius, gratis)."""
    stats = {"subs": 0, "trades": 0}
    while True:
        try:
            async with websockets.connect(HELIUS + key, ping_interval=20, max_size=None) as ws:
                pending, subs, rid = {}, {}, 0
                for mint, c in list(coins.items()):
                    if c.curve:
                        hq.put_nowait(("sub", mint, c.curve))
                print(f"helius conectado ({len(coins)} monedas abiertas)", file=sys.stderr)

                async def sender():
                    nonlocal rid
                    while True:
                        op = await hq.get()
                        rid += 1
                        if op[0] == "sub":
                            pending[rid] = op[1]
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "logsSubscribe",
                                                      "params": [{"mentions": [op[2]]}, {"commitment": "confirmed"}]}))
                        elif op[1] in subs:
                            await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": "logsUnsubscribe",
                                                      "params": [subs.pop(op[1])]}))
                st = asyncio.create_task(sender())
                try:
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("id") in pending:
                            mint = pending.pop(m["id"])
                            if "result" in m:
                                subs[mint] = m["result"]; stats["subs"] += 1
                            elif stats["subs"] == 0:
                                print(f"helius error al suscribir: {m.get('error')}", file=sys.stderr)
                        elif m.get("method") == "logsNotification":
                            v = m["params"]["result"]["value"]
                            if v.get("err"):
                                continue
                            for line in v.get("logs", []):
                                if line.startswith("Program data: "):
                                    ev = decode_trade(line[14:])
                                    if ev and ev["mint"] in coins:
                                        coins[ev["mint"]].add_trade(ev, time.time())
                                        stats["trades"] += 1
                                        if stats["trades"] in (1, 100, 1000) or stats["trades"] % 10000 == 0:
                                            print(f"helius: {stats['trades']} trades ({stats['subs']} suscripciones)", file=sys.stderr)
                finally:
                    st.cancel()
        except Exception as e:
            print(f"helius perdido ({e}), reconectando en 5 s...", file=sys.stderr)
            await asyncio.sleep(5)


async def run(a):
    coins, rows, cross_rows = {}, [], []
    light_count = 0
    hq = asyncio.Queue()
    key = os.environ.get("HELIUS_API_KEY")
    if key:
        asyncio.create_task(helius_trades(key, coins, hq))
    else:
        print("sin HELIUS_API_KEY: solo graduadas, sin trades por moneda", file=sys.stderr)
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
                        if key and coins[mint].curve:
                            hq.put_nowait(("sub", mint, coins[mint].curve))
                    elif mint in coins and m.get("txType") in ("buy", "sell"):
                        coins[mint].add(m, now)
                    # al minuto: las de pocos compradores se dejan de seguir (casi todas mueren; ahorra Helius)
                    for k, c in coins.items():
                        if not c.checked and now - c.t0 >= a.entry + 5:
                            c.checked = True
                            if len(c.buyers) < a.min_buyers:
                                c.light = True
                                if key:
                                    hq.put_nowait(("unsub", k))
                    # cerrar las que ya cumplieron el tiempo
                    done = [k for k, c in coins.items() if now - c.t0 >= a.track * 60
                            and (c.crossed is None or now - c.t0 >= c.crossed + a.track_cross * 60)]
                    for k in done:
                        c = coins.pop(k)
                        if c.light:
                            light_count += 1
                            continue
                        if key:
                            hq.put_nowait(("unsub", k))
                        if c.crossed is not None:
                            rc = evaluate(c, a, c.crossed, c.crossed + a.track_cross * 60)
                            if rc:
                                cross_rows.append(rc)
                                print(f"  [cruce] {c.name}: max {rc['max']:.1f}x desde la entrada, neto {(rc['mult']-1)*100:+.0f}%", file=sys.stderr)
                        r = evaluate(c, a, a.entry, a.track * 60)
                        if r is None:   # nadie opero despues del minuto: no habia a quien comprarle
                            continue
                        r.update(buyers=len(c.buyers), dev_sold=c.dev_sold)
                        rows.append(r)
                        w.writerow([c.mint, c.name, time.strftime("%Y-%m-%d %H:%M", time.localtime(c.t0)),
                                    r["buyers"], int(r["dev_sold"]), round(r["mult"], 4), round(r["max"], 3), int(r["half"])])
                        f.flush()
                    if now - last_print >= a.every * 60:
                        print(f"(siguiendo {len(coins)} monedas abiertas; descartadas por <{a.min_buyers} compradores: {light_count})", file=sys.stderr)
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
    ap.add_argument("--min-buyers", type=int, default=5, help="compradores en el 1er minuto para seguir la moneda")
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
