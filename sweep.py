#!/usr/bin/env python3
"""Barrido de entradas x salidas sobre graduadas. Elige en la mitad vieja, valida en la nueva."""
import argparse, itertools, random, sys, time
import memes as M


def enter_delay(c, minutes, created):
    for i, r in enumerate(c):
        if r[0] >= created + minutes * 60:
            return i, r[4]
    return None


def run(cfg, c, created, a):
    kind, p = cfg["entry"]
    if kind == "dip":
        a.dip_min, a.dip_max, a.vol_filter = p[0], p[1], False
        return M.simulate(c, a)
    if kind == "cross":
        return M.simulate_cross(c, a, p)
    e = enter_delay(c, p, created)
    return M.exit_atm(c, e[0], e[1], a) if e else None


def stats(ms):
    if not ms:
        return None, 0
    return sum(ms) / len(ms) - 1, len(ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mints", default="graduadas.csv")
    ap.add_argument("--fee", type=float, default=0.02)
    ap.add_argument("--budget", type=float, default=1200)
    a = ap.parse_args()
    a.bounce = 0.10
    t0 = time.time()
    ps = M.pools_from_file(a.mints)
    data = []
    for addr, name, created in ps:
        if time.time() - t0 > a.budget:
            break
        c = M.candles(addr, created)
        if len(c) >= 2:
            data.append((created, name, c))
    data.sort()
    half = len(data) // 2
    A, B = data[:half], data[half:]
    print(f"monedas: {len(data)} | mitad 1 (eleccion): {len(A)} | mitad 2 (validacion): {len(B)} | fee {a.fee} por lado\n")
    entries = [("delay", 0), ("delay", 5), ("delay", 15), ("delay", 30),
               ("dip", (0.2, 0.4)), ("dip", (0.3, 0.5)), ("dip", (0.4, 0.6)), ("cross", 100_000)]
    exits = list(itertools.product([2.0, 3.0], [0.2, 0.3, 0.5], [0.3, 0.4, 0.6]))
    results = []
    for e, (tp, tr, sl) in itertools.product(entries, exits):
        a.tp, a.trail, a.stop = tp, tr, sl
        cfg = {"entry": e}
        mA = [r[0] for cr, n, c in A if (r := run(cfg, c, cr, a))]
        mB = [r[0] for cr, n, c in B if (r := run(cfg, c, cr, a))]
        sA, nA = stats(mA); sB, nB = stats(mB)
        if sA is None or nA < 20:
            continue
        srt = sorted(mA, reverse=True)
        noTop3 = sum(srt[3:]) / len(srt[3:]) - 1 if len(srt) > 3 else None
        results.append((sA, noTop3, nA, sB, nB, e, tp, tr, sl))
    results.sort(key=lambda x: -x[0])
    fmt = lambda v: "   -   " if v is None else f"{v*100:+6.1f}%"
    print(f"{'entrada':<16}{'vende mitad en':>15}{'trailing':>9}{'stop':>6}{'M1 medio':>10}{'M1 sin top3':>12}{'n1':>5}{'M2 medio':>10}{'n2':>5}")
    for sA, nt, nA, sB, nB, e, tp, tr, sl in results[:15]:
        print(f"{str(e):<16}{tp:>13.0f}x{tr*100:>8.0f}%{sl*100:>5.0f}%{fmt(sA):>10}{fmt(nt):>12}{nA:>5}{fmt(sB):>10}{nB:>5}")
    pos_B = sum(1 for r in results[:15] if r[3] is not None and r[3] > 0)
    print(f"\nDe las 15 mejores en la mitad 1, {pos_B} siguen positivas en la mitad 2.")
    print(f"Promedio en mitad 2 de esas 15: {fmt(sum(r[3] for r in results[:15] if r[3] is not None)/15)}")
    print(f"Promedio en mitad 2 de TODAS las {len(results)} combinaciones: {fmt(sum(r[3] for r in results if r[3] is not None)/len(results))}")


if __name__ == "__main__":
    main()
