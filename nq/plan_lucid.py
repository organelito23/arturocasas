#!/usr/bin/env python3
"""Plan del video @abel.nq en LucidFlex 25K, con datos reales 2014-2026.
Examen: +$625/dia arriesgando $1000 (2 dias = $1250). Fondeada: un dia grande ($1000 -> +$2000, 1 mini)
y despues 'moneda' a las 15:00 (3 micros, objetivo +$100) hasta 5 dias de +$100; retiro 50% del beneficio
(max $1000, min $500, llega el 90%); maximo 4 retiros. Senal: Fibonacci 0.618 del rango de ayer (o empujon)."""
import random, datetime as dt
import nqlib as L
import strategies as S

days = L.load("data/nq_*.csv.gz")
cut = next(i for i, d in enumerate(days) if d.date >= dt.date(2022, 1, 1))


def fib(stop):
    def f(days_, i):
        if i == 0:
            return []
        d, p = days_[i], days_[i - 1]
        hi, lo = max(p.h), min(p.l)
        lvl = hi - 0.618 * (hi - lo)
        o = d.o[0]
        if abs(o - lvl) < 1:
            return []
        dr = 1 if o > lvl else -1           # llega bajando -> compra ; llega subiendo -> vende
        for k in range(1, len(d.t)):
            if d.t[k] >= 360:
                return []
            if (dr > 0 and d.l[k] <= lvl) or (dr < 0 and d.h[k] >= lvl):
                return [dict(i=k, dir=dr, px=lvl, stop_pts=stop, target_pts=None, exit_min=390)]
        return []
    return f


def emp(stop):
    def f(days_, i):
        s = S.empujon(days_, i)
        return [dict(s[0], stop_pts=stop, target_pts=None, exit_min=390)] if s else []
    return f


def coin(seed):
    def f(days_, i):
        d = days_[i]; k = d.at(330)                 # 15:00 NY
        if k is None:
            return []
        dr = 1 if random.Random(f"{seed}-{d.date}").random() < 0.5 else -1
        return [dict(i=k, dir=dr, px=d.o[k], stop_pts=300, target_pts=None, exit_min=390)]
    return f


def lucid_eval(res, s):
    bal, peak = 25000.0, 25000.0
    for k in range(s, min(len(res), s + 30)):
        r = res[k]
        if not r:
            continue
        pnl, worst = r
        if bal + worst <= peak - 1000:
            return "fail", k
        bal += pnl
        if bal <= peak - 1000:
            return "fail", k
        if bal >= 26250:
            return "pass", k
        peak = max(peak, bal)
    return "timeout", s + 30


def lucid_funded(big, small, s, max_pay=4):
    bal, peak, thr = 25000.0, 25000.0, 24000.0
    mode_big, good, cyc, paid, n = True, 0, 0.0, 0.0, 0
    for k in range(s, min(len(big), s + 160)):
        r = (big if mode_big else small)[k]
        if not r:
            continue
        pnl, worst = r
        if bal + worst <= thr:
            return paid, n, True
        bal += pnl
        if bal <= thr:
            return paid, n, True
        peak = max(peak, bal); thr = 25100.0 if peak >= 26100 else max(thr, peak - 1000)
        if mode_big and pnl > 0:
            mode_big = False
        cyc += pnl; good += pnl >= 100
        prof = bal - 25000
        if good >= 5 and cyc > 0 and 0.5 * prof >= 500:
            amt = min(1000, 0.5 * prof)
            paid += 0.9 * amt; n += 1; bal -= amt; good = 0; cyc = 0.0; thr = 25100.0
            if n >= max_pay:
                return paid, n, False
    return paid, n, False


def run(label, sig):
    ev = L.day_results(days, sig(25), 1000, 625)          # 20 micros, stop 25 pts, objetivo $625
    big = L.day_results(days, sig(50), 1000, 2000)        # 10 micros (1 mini), stop 50 pts, objetivo $2000
    for per, lo, hi in (("2014-21", 25, cut - 170), ("2022-26", cut, len(days) - 170)):
        cnt = ok = 0; pays = []; burns = 0; npay = 0
        for s in range(lo, hi, 2):
            st, end = lucid_eval(ev, s); cnt += 1
            if st != "pass":
                continue
            ok += 1
            small = L.day_results(days, coin(s), 100, 100)   # riesgo nominal 100/(300pt*2) -> forzamos 3 micros abajo
            p, n, b = lucid_funded(big, small, end + 1)
            pays.append(p); npay += n; burns += b
        avg = sum(pays) / len(pays) if pays else 0
        print(f"{label:<28} {per}: pasa {ok/cnt*100:4.1f}% | fondeada: cobra ${avg:,.0f} netos ({npay/max(ok,1):.2f} retiros, quema {burns/max(ok,1)*100:.0f}%) | cobrado por cuenta comprada ${ok/cnt*avg:,.0f}")


L.contracts_for_orig = L.contracts_for
def contracts_for(risk, stop):     # la 'moneda' usa siempre 3 micros
    if stop == 300:
        return 3
    return L.contracts_for_orig(risk, stop)
L.contracts_for = contracts_for

run("Video: Fibonacci 0.618", fib)
run("Mismo plan con el empujon", emp)
