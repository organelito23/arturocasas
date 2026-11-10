"""Estrategias intradia NQ con reglas FIJAS (sin optimizar). Minutos contados desde 9:30 NY (15:50 = 380, 16:00 = 390)."""
import random
from nqlib import adr

NOON = 150


def empujon(days, i):
    """Empujon de apertura NY (playbook propio): movimiento 9:30->9:45 >= 15% ADR -> a favor, stop 10% ADR, TP 0.75R, salida 15:50."""
    d = days[i]; a = adr(days, i)
    e, last = d.at(15), d.at(14)
    if not a or e is None or last is None:
        return []
    move = d.c[last] - d.o[0]
    if abs(move) < 0.15 * a:
        return []
    return [dict(i=e, dir=1 if move > 0 else -1, px=d.o[e], stop_pts=0.10 * a, target_pts=0.075 * a, exit_min=380)]


def orb5(days, i):
    """Zarattini & Aziz: direccion de la 1a vela de 5 min, entrada 9:35, stop extremo opuesto, TP 10R, salida al cierre."""
    d = days[i]; e = d.at(5)
    if e is None:
        return []
    o, c = d.o[0], d.c[e - 1]
    hi, lo = max(d.h[:e]), min(d.l[:e])
    if c == o:
        return []
    dr = 1 if c > o else -1
    px = d.o[e]
    stop = (px - lo) if dr > 0 else (hi - px)
    if stop <= 2:
        return []
    return [dict(i=e, dir=dr, px=px, stop_pts=stop, target_pts=10 * stop, exit_min=390)]


def _breakout(d, hi, lo, start, stop_fn, tgt_fn, exit_min=380, until=NOON):
    for k in range(start, len(d.t)):
        if d.t[k] >= until:
            return []
        if d.h[k] > hi:
            px = max(d.o[k], hi); s = stop_fn(px, 1)
            return [dict(i=k, dir=1, px=px, stop_pts=s, target_pts=tgt_fn(s), exit_min=exit_min)] if s > 2 else []
        if d.l[k] < lo:
            px = min(d.o[k], lo); s = stop_fn(px, -1)
            return [dict(i=k, dir=-1, px=px, stop_pts=s, target_pts=tgt_fn(s), exit_min=exit_min)] if s > 2 else []
    return []


def orb15(days, i):
    """Ruptura del rango 9:30-9:45 (hasta 12:00), stop a mitad del rango, TP 2R, salida 15:50."""
    d = days[i]; e = d.at(15)
    if e is None:
        return []
    hi, lo = max(d.h[:e]), min(d.l[:e]); mid = (hi + lo) / 2
    return _breakout(d, hi, lo, e, lambda px, dr: abs(px - mid), lambda s: 2 * s)


def orb30(days, i):
    """Ruptura del rango 9:30-10:00 (hasta 12:00), stop extremo opuesto, TP = ancho del rango, salida 15:50."""
    d = days[i]; e = d.at(30)
    if e is None:
        return []
    hi, lo = max(d.h[:e]), min(d.l[:e]); w = hi - lo
    return _breakout(d, hi, lo, e, lambda px, dr: (px - lo) if dr > 0 else (hi - px), lambda s: w)


def pdhl(days, i):
    """Ruptura del maximo/minimo de la sesion anterior (hasta 12:00), stop 10% ADR, TP 2R, salida 15:50."""
    if i == 0:
        return []
    d, p = days[i], days[i - 1]; a = adr(days, i)
    if not a:
        return []
    hi, lo = max(p.h), min(p.l)
    if not (lo < d.o[0] < hi):
        return []
    return _breakout(d, hi, lo, 0, lambda px, dr: 0.10 * a, lambda s: 2 * s)


def gapfade(days, i):
    """Gap de 0.3%-1.5%: en contra del gap a las 9:30, TP = cierre anterior, stop = tamano del gap, salida 12:00."""
    d = days[i]; g = d.o[0] - d.prev_close
    if not (0.003 <= abs(g) / d.prev_close <= 0.015):
        return []
    return [dict(i=0, dir=-1 if g > 0 else 1, px=d.o[0], stop_pts=abs(g), target_pts=abs(g), exit_min=NOON)]


def gao(days, i):
    """Gao et al. (2018): retorno cierre anterior -> 10:00 decide la direccion de 15:30 a 16:00. Stop 10% ADR."""
    d = days[i]; a = adr(days, i); k10, e = d.at(29), d.at(360)
    if not a or k10 is None or e is None:
        return []
    r = d.c[k10] / d.prev_close - 1
    if r == 0:
        return []
    return [dict(i=e, dir=1 if r > 0 else -1, px=d.o[e], stop_pts=0.10 * a, target_pts=None, exit_min=390)]


# ---------- "noise area" de Zarattini, Barbon & Aziz (2024), revisa cada 30 min
_moves = {}


def _move_profile(d):
    if id(d) not in _moves:
        o = d.o[0]
        _moves[id(d)] = {t: abs(d.c[k] / o - 1) for t, k in d.idx.items()}
    return _moves[id(d)]


def noise(days, i):
    if i < 14:
        return []
    d = days[i]
    prof = [_move_profile(days[j]) for j in range(i - 14, i)]
    o, pc = d.o[0], d.prev_close
    pv = cv = 0.0          # VWAP con volumen 1 (tick volume no disponible fiable): promedio de precio tipico
    vwap = []
    for k in range(len(d.t)):
        tp = (d.h[k] + d.l[k] + d.c[k]) / 3
        pv += tp; cv += 1; vwap.append(pv / cv)

    def bands(t):
        vals = [p[t] for p in prof if t in p]
        s = sum(vals) / len(vals) if vals else 0.0
        return max(o, pc) * (1 + s), min(o, pc) * (1 - s)

    trades, pos, entry, ent_k, mae, stop_ref = [], 0, 0.0, 0, 0.0, 0.0
    for t in range(29, 389, 30):          # velas que cierran a las 10:00, 10:30 ... 15:30
        k = d.at(t)
        if k is None or k + 1 >= len(d.t):
            continue
        # MAE entre revisiones
        if pos:
            for j in range(ent_k, k + 1):
                mae = max(mae, (entry - d.l[j]) if pos > 0 else (d.h[j] - entry))
            ent_k = k + 1
        px = d.c[k]; ub, lb = bands(t)
        nxt = d.o[k + 1]
        if pos > 0 and px < max(ub, vwap[k]) or pos < 0 and px > min(lb, vwap[k]):
            trades.append(_closed(pos, entry, nxt, mae, stop_ref)); pos = 0
        if pos == 0:
            if px > ub:
                pos, entry, ent_k, mae = 1, nxt, k + 1, 0.0
                stop_ref = max(5.0, nxt - max(ub, vwap[k]))
            elif px < lb:
                pos, entry, ent_k, mae = -1, nxt, k + 1, 0.0
                stop_ref = max(5.0, min(lb, vwap[k]) - nxt)
    if pos:
        for j in range(ent_k, len(d.t)):
            mae = max(mae, (entry - d.l[j]) if pos > 0 else (d.h[j] - entry))
        trades.append(_closed(pos, entry, d.c[-1], mae, stop_ref))
    return trades


def _closed(pos, entry, exitpx, mae, stop_ref):
    res = {"pts": pos * (exitpx - entry), "mae": mae, "exit": "regla"}
    return dict(i=0, dir=pos, px=entry, stop_pts=stop_ref, fn=lambda r=res: dict(r))


ALL = {"empujon_ny": empujon, "orb5_zarattini": orb5, "orb15": orb15, "orb30": orb30,
       "max_min_ayer": pdhl, "gap_fade": gapfade, "gao_ultima_media_hora": gao, "noise_area": noise}


def randomized(strategy, seed):
    """Misma entrada, stop y objetivo, pero direccion al azar (para comparar contra suerte)."""
    def f(days, i):
        out = []
        rng = random.Random(f"{seed}-{days[i].date}")
        for s in strategy(days, i):
            if "fn" in s:      # noise: invertir resultado
                if rng.random() < 0.5:
                    r = s["fn"](); r["pts"] = -r["pts"]
                    s = dict(s, dir=-s["dir"], fn=lambda r=r: dict(r))
                out.append(s)
            else:
                out.append(dict(s, dir=s["dir"] if rng.random() < 0.5 else -s["dir"]))
        return out
    return f
