#!/usr/bin/env python3
"""Corre todas las estrategias: capital propio (riesgo fijo $500) y fondeo Apex 25K EOD (examen + cuenta fondeada).
Periodo de eleccion 2014-2021, validacion 2022-2026. Comparacion contra la misma regla con direccion al azar."""
import sys, statistics, datetime as dt
import nqlib as L
import strategies as S

days = L.load(sys.argv[1] if len(sys.argv) > 1 else "data/nq_*.csv.gz")
cut = next(i for i, d in enumerate(days) if d.date >= dt.date(2022, 1, 1))
print(f"dias RTH: {len(days)} ({days[0].date} a {days[-1].date}); validacion desde indice {cut}\n")


def trades(strategy, lo, hi):
    out = []
    for i in range(lo, hi):
        for spec in strategy(days, i):
            out.append(L.sim(days[i], spec))
    return out


def fmt_m(m):
    if not m:
        return "sin trades"
    return f"n={m['n']:<5} WR {m['wr']*100:4.1f}%  R medio {m['avgR']:+.3f}  PF {m['pf']:.2f}  total ${m['total']:>9,.0f}  maxDD ${m['maxdd']:>8,.0f}"


rows = []
for name, st in S.ALL.items():
    print(f"=== {name} ===")
    res = {}
    for label, lo, hi in (("2014-2021", 0, cut), ("2022-2026", cut, len(days))):
        m = L.metrics(trades(st, lo, hi))
        rnd = [L.metrics(trades(S.randomized(st, s), lo, hi)) for s in range(5)]
        rR = statistics.mean(r["avgR"] for r in rnd if r)
        res[label] = (m, rR)
        print(f"  capital propio {label}: {fmt_m(m)}   | azar R medio {rR:+.3f}")
    for mode, (re, ft, rp) in {"un disparo ($450 -> +$1550)": (450, 1550, 150),
                               "normal ($250, objetivo propio)": (250, None, 150)}.items():
        for label, lo, hi in (("2014-2021", 0, cut), ("2022-2026", cut, len(days) - 30)):
            pr = L.prop_report(days, st, re, ft, rp, lo, hi)
            rnd = L.prop_report(days, S.randomized(st, 1), re, ft, rp, lo, hi)
            print(f"  fondeo {mode:<32} {label}: pasa {pr['pass_rate']*100:4.1f}% (azar {rnd['pass_rate']*100:4.1f}%)"
                  f"  dias med {pr['days_to_pass']}  | PA: retiro medio ${pr['pa_payout_avg']:,.0f}"
                  f" ({pr['pa_npay_avg']:.1f} retiros, quema {pr['pa_burn']*100:.0f}%)  | $ esperado por intento ${pr['exp_per_attempt']:,.0f}"
                  f" (azar ${rnd['exp_per_attempt']:,.0f})")
            rows.append((name, mode, label, pr, rnd))
    print()
