#!/usr/bin/env python3
"""Busqueda amplia: ~130 reglas intradia NQ con parametros fijos de una rejilla.
Real = R medio > 0 en 2014-2021 Y en 2022-2026 (por separado), con t > 2 en el total."""
import math, sys, datetime as dt, itertools, statistics
import nqlib as L

days = L.load("data/nq_*.csv.gz")
cut = next(i for i, d in enumerate(days) if d.date >= dt.date(2022, 1, 1))
ADR = [L.adr(days, i) for i in range(len(days))]
EOD = 390


def vwap_at(d, k):
    s = 0.0
    for j in range(k + 1):
        s += (d.h[j] + d.l[j] + d.c[j]) / 3
    return s / (k + 1)


def mk(entry_fn):
    """entry_fn(d, i, a) -> spec o None"""
    def f(days_, i):
        a = ADR[i]
        if not a:
            return []
        s = entry_fn(days_[i], i, a)
        return [s] if s and s["stop_pts"] > 2 else []
    return f


S = {}

# A. ORB
for N, mode, stopm, tgt in itertools.product((5, 15, 30, 60), ("ruptura", "vela"), ("opuesto", "mitad"), (1, 2, None)):
    def e(d, i, a, N=N, mode=mode, stopm=stopm, tgt=tgt):
        k0 = d.at(N)
        if k0 is None:
            return None
        hi, lo = max(d.h[:k0]), min(d.l[:k0]); mid = (hi + lo) / 2
        if mode == "vela":
            dr = 1 if d.c[k0 - 1] > d.o[0] else -1 if d.c[k0 - 1] < d.o[0] else 0
            if not dr:
                return None
            px, k = d.o[k0], k0
        else:
            k = next((j for j in range(k0, len(d.t)) if d.t[j] < 150 and (d.h[j] > hi or d.l[j] < lo)), None)
            if k is None:
                return None
            dr = 1 if d.h[k] > hi else -1
            px = max(d.o[k], hi) if dr > 0 else min(d.o[k], lo)
        ref = (lo if dr > 0 else hi) if stopm == "opuesto" else mid
        st = abs(px - ref)
        return dict(i=k, dir=dr, px=px, stop_pts=st, target_pts=tgt * st if tgt else None, exit_min=380)
    S[f"ORB{N} {mode} stop {stopm} obj {tgt or 'cierre'}"] = mk(e)

# B. momentum por horario: direccion 9:30->T1 operada desde T1
for T1, hold in itertools.product((30, 60, 90, 150, 270), ("cierre", "60min")):
    def e(d, i, a, T1=T1, hold=hold):
        k = d.at(T1)
        if k is None:
            return None
        mv = d.c[k - 1] - d.o[0]
        if mv == 0:
            return None
        return dict(i=k, dir=1 if mv > 0 else -1, px=d.o[k], stop_pts=0.10 * a, target_pts=None,
                    exit_min=EOD if hold == "cierre" else T1 + 60)
    S[f"momentum 9:30->+{T1}min, salida {hold}"] = mk(e)

# C. reversion: movimiento grande 9:30->T1 en contra
for T1, kk, ex in itertools.product((30, 60, 90), (0.2, 0.3, 0.4), ("12:00", "cierre")):
    def e(d, i, a, T1=T1, kk=kk, ex=ex):
        k = d.at(T1)
        if k is None:
            return None
        mv = d.c[k - 1] - d.o[0]
        if abs(mv) < kk * a:
            return None
        return dict(i=k, dir=-1 if mv > 0 else 1, px=d.o[k], stop_pts=0.10 * a, target_pts=abs(mv),
                    exit_min=150 if ex == "12:00" else EOD)
    S[f"reversion >{kk} ADR a +{T1}min, salida {ex}"] = mk(e)

# D. gaps
for band, side, ex in itertools.product(((0.002, 0.005), (0.005, 0.01), (0.01, 0.05)), ("seguir", "contra"), (60, 150, EOD)):
    def e(d, i, a, band=band, side=side, ex=ex):
        g = (d.o[0] - d.prev_close) / d.prev_close
        if not (band[0] <= abs(g) < band[1]):
            return None
        dr = (1 if g > 0 else -1) * (1 if side == "seguir" else -1)
        return dict(i=0, dir=dr, px=d.o[0], stop_pts=0.10 * a, target_pts=None, exit_min=ex)
    S[f"gap {band[0]*100:.1f}-{band[1]*100:.1f}% {side}, salida +{ex}min"] = mk(e)

# E. VWAP
for T, kk, side in itertools.product((60, 120, 240), (0.1, 0.2), ("seguir", "contra")):
    def e(d, i, a, T=T, kk=kk, side=side):
        k = d.at(T)
        if k is None:
            return None
        v = vwap_at(d, k - 1); px = d.c[k - 1]
        if abs(px - v) < kk * a:
            return None
        dr = (1 if px > v else -1) * (1 if side == "seguir" else -1)
        return dict(i=k, dir=dr, px=d.o[k], stop_pts=0.10 * a, target_pts=abs(px - v) if side == "contra" else None, exit_min=EOD)
    S[f"VWAP a +{T}min dist>{kk} ADR {side}"] = mk(e)

# F. dia de la semana: comprar apertura, vender cierre
for wd in range(5):
    def e(d, i, a, wd=wd):
        if d.date.weekday() != wd:
            return None
        return dict(i=0, dir=1, px=d.o[0], stop_pts=0.15 * a, target_pts=None, exit_min=EOD)
    S[f"largo todo el dia los {['lun','mar','mie','jue','vie'][wd]}"] = mk(e)

# G. apertura respecto al rango de ayer
for side in ("seguir", "contra"):
    def e(d, i, a, side=side):
        if i == 0:
            return None
        p = days[i - 1]; hi, lo = max(p.h), min(p.l); k = d.at(15)
        px = d.c[k - 1] if k else None
        if px is None or lo <= px <= hi:
            return None
        dr = (1 if px > hi else -1) * (1 if side == "seguir" else -1)
        return dict(i=k, dir=dr, px=d.o[k], stop_pts=0.10 * a, target_pts=None, exit_min=EOD)
    S[f"9:45 fuera del rango de ayer, {side}"] = mk(e)

# H. NR7 + ruptura ORB30
for tgt in (1, None):
    def e(d, i, a, tgt=tgt):
        if i < 7:
            return None
        rng = [max(days[j].h) - min(days[j].l) for j in range(i - 7, i)]
        if rng[-1] != min(rng):
            return None
        k0 = d.at(30); hi, lo = max(d.h[:k0]), min(d.l[:k0])
        k = next((j for j in range(k0, len(d.t)) if d.t[j] < 150 and (d.h[j] > hi or d.l[j] < lo)), None)
        if k is None:
            return None
        dr = 1 if d.h[k] > hi else -1; px = hi if dr > 0 else lo; st = hi - lo
        return dict(i=k, dir=dr, px=px, stop_pts=st, target_pts=tgt * st if tgt else None, exit_min=380)
    S[f"NR7 + ruptura ORB30 obj {tgt or 'cierre'}"] = mk(e)


def Rs(st, lo, hi):
    out = []
    for i in range(lo, hi):
        for spec in st(days, i):
            t = L.sim(days[i], spec)
            n = L.contracts_for(500, t["stop_pts"])
            if n:
                out.append(L.dollars(t["pts"], n) / 500)
    return out


def tstat(r):
    return statistics.mean(r) / (statistics.pstdev(r) / math.sqrt(len(r))) if len(r) > 2 and statistics.pstdev(r) > 0 else 0


rows = []
for name, st in S.items():
    a, b = Rs(st, 0, cut), Rs(st, cut, len(days))
    if len(a) < 100 or len(b) < 50:
        continue
    rows.append((name, statistics.mean(a), len(a), statistics.mean(b), len(b), tstat(a + b)))
print(f"reglas probadas: {len(rows)}\n")
rows.sort(key=lambda x: -x[1])
print("TOP 15 en 2014-2021 (eleccion) y como les fue en 2022-2026:")
for n, ma, na, mb, nb, t in rows[:15]:
    print(f"  {n:<44} 14-21 {ma:+.3f}R (n={na})  ->  22-26 {mb:+.3f}R (n={nb})")
print("\nGANAN EN LOS DOS PERIODOS por separado:")
both = [r for r in rows if r[1] > 0 and r[3] > 0]
for n, ma, na, mb, nb, t in sorted(both, key=lambda x: -x[5]):
    print(f"  {n:<44} 14-21 {ma:+.3f}R  22-26 {mb:+.3f}R  t total {t:+.2f}{'  <-- t>2' if t > 2 else ''}")
print(f"\n{len(both)} de {len(rows)} ganan en ambos; por azar se esperarian ~{len(rows)//4}.")
