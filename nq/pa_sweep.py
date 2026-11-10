#!/usr/bin/env python3
"""Cuenta fondeada (PA) Apex 25K: cuanto se retira segun estrategia, riesgo y objetivo fijo.
Arranca una PA en cada dia del periodo y promedia. Elige en 2014-2021, valida en 2022-2026."""
import sys, datetime as dt
import nqlib as L
import strategies as S

days = L.load("data/nq_*.csv.gz")
cut = next(i for i, d in enumerate(days) if d.date >= dt.date(2022, 1, 1))
END = len(days) - 120


def pa_stats(res, lo, hi, step=3):
    tot = n = burn = cnt = 0
    for s in range(lo, hi, step):
        p, k, b = L.sim_pa(res, s)
        tot += p; n += k; burn += b; cnt += 1
    return tot / cnt, n / cnt, burn / cnt


def exam_pass(res, lo, hi, step=3):
    ok = cnt = 0
    for s in range(lo, hi, step):
        st, _, _ = L.sim_eval(res, s)
        ok += st == "pass"; cnt += 1
    return ok / cnt


print(f"{'estrategia':<22}{'riesgo':>7}{'obj fijo':>9} | {'2014-21 retiro':>15}{'quema':>7} | {'2022-26 retiro':>15}{'quema':>7}")
best = {}
for name, st in S.ALL.items():
    for risk in (100, 200, 300, 450):
        for tgt in (None, 150, 300):
            res = L.day_results(days, st, risk, tgt)
            a = pa_stats(res, 20, cut - 120)
            b = pa_stats(res, cut, END)
            print(f"{name:<22}{risk:>7}{str(tgt):>9} | ${a[0]:>13,.0f}{a[2]*100:>6.0f}% | ${b[0]:>13,.0f}{b[2]*100:>6.0f}%")
            if name not in best or a[0] > best[name][0]:
                best[name] = (a[0], risk, tgt, b)
    sys.stdout.flush()

print("\nMejor configuracion de PA por estrategia (elegida en 2014-2021) y como le fue en 2022-2026:")
for name, (a, risk, tgt, b) in best.items():
    print(f"  {name:<22} riesgo ${risk}, objetivo {tgt}: 2014-21 ${a:,.0f} -> 2022-26 ${b[0]:,.0f} (retiros {b[1]:.2f}, quema {b[2]*100:.0f}%)")

# referencia: moneda al aire +-$150 por dia
import random
res = []
rng = random.Random(1)
for _ in range(len(days)):
    res.append((150.0, 0.0) if rng.random() < 0.5 else (-150.0, -150.0))
print(f"\nReferencia moneda al aire +-$150/dia: retiro medio ${pa_stats(res, 20, END)[0]:,.0f}")
