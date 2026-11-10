"""
Libreria de backtest intradia NQ (MNQ) + simulador de cuentas de fondeo Apex 25K EOD.

Datos: velas 1m USATECH (Dukascopy, UTC) -> sesiones RTH 9:30-16:00 hora NY.
Costos: $3.50 por contrato ida y vuelta + 1 tick (0.25 pt) de slippage por contrato.
MNQ = $2 por punto. Maximo 40 micros (Apex 25K).
"""
import gzip, csv, glob, math, random
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
PT_VALUE = 2.0
COST_RT = 3.50          # comision por contrato ida y vuelta
SLIP_PTS = 0.25         # 1 tick por contrato (total ida y vuelta)
MAX_CONTRACTS = 40


# ------------------------------------------------------------------ datos
class Day:
    __slots__ = ("date", "t", "o", "h", "l", "c", "prev_close", "full_hi", "full_lo", "idx")

    def __init__(self, date):
        self.date, self.t, self.o, self.h, self.l, self.c = date, [], [], [], [], []
        self.prev_close = None
        self.full_hi, self.full_lo = -1e18, 1e18
        self.idx = {}

    def at(self, hhmm):
        """indice de la vela que empieza en hh:mm (minutos desde 9:30)."""
        return self.idx.get(hhmm)


def load(pattern):
    """Devuelve lista de Day (solo dias con sesion RTH completa)."""
    by_date = {}
    full = {}
    for fn in sorted(glob.glob(pattern)):
        with gzip.open(fn, "rt") as f:
            r = csv.reader(f)
            next(r)
            for row in r:
                ts = int(row[0]) / 1000
                dt = datetime.fromtimestamp(ts, timezone.utc).astimezone(NY)
                o, h, l, c = map(float, row[1:5])
                d = dt.date()
                fh = full.setdefault(d, [-1e18, 1e18])
                fh[0] = max(fh[0], h); fh[1] = min(fh[1], l)
                m = dt.hour * 60 + dt.minute
                if 570 <= m < 960:        # 9:30 .. 15:59
                    day = by_date.get(d)
                    if day is None:
                        day = by_date[d] = Day(d)
                    day.idx[m - 570] = len(day.t)
                    day.t.append(m - 570); day.o.append(o); day.h.append(h); day.l.append(l); day.c.append(c)
    days = [by_date[d] for d in sorted(by_date)
            if d.weekday() < 5 and len(by_date[d].t) >= 380 and 0 in by_date[d].idx
            and max(by_date[d].h) - min(by_date[d].l) > 0.002 * by_date[d].o[0]]   # sin fines de semana ni dias planos/feriados
    for prev, day in zip(days, days[1:]):
        day.prev_close = prev.c[-1]
    for d in days:
        d.full_hi, d.full_lo = full[d.date]
    days = [d for d in days if d.prev_close]
    return days


def adr(days, i, n=20):
    """rango promedio (dia completo) de los n dias ANTERIORES a i."""
    if i < n:
        return None
    return sum(days[j].full_hi - days[j].full_lo for j in range(i - n, i)) / n


# ------------------------------------------------------------------ ejecucion de un trade
def run_trade(day, entry_i, direction, entry_px, stop_pts, target_pts=None, exit_min=380, trail=None):
    """Simula un trade sobre las velas 1m desde entry_i (la entrada ocurre al precio entry_px dentro/al inicio de esa vela).
    direction +1 largo / -1 corto. Si stop y target caen en la misma vela, cuenta el stop (conservador).
    Devuelve dict(pts, mae_pts, exit).  mae_pts = peor punto en contra (positivo)."""
    stop = entry_px - direction * stop_pts
    target = entry_px + direction * target_pts if target_pts else None
    mae = 0.0
    for k in range(entry_i, len(day.t)):
        if day.t[k] >= exit_min:
            px = day.o[k]
            return {"pts": direction * (px - entry_px), "mae": mae, "exit": "tiempo"}
        h, l = day.h[k], day.l[k]
        adverse = (entry_px - l) if direction > 0 else (h - entry_px)
        mae = max(mae, adverse)
        hit_stop = (l <= stop) if direction > 0 else (h >= stop)
        if hit_stop:
            o = day.o[k]
            px = (min(o, stop) if direction > 0 else max(o, stop)) if k > entry_i else stop
            pts = direction * (px - entry_px)
            return {"pts": pts, "mae": max(mae, -pts), "exit": "stop"}
        if target is not None and ((h >= target) if direction > 0 else (l <= target)):
            return {"pts": target_pts, "mae": mae, "exit": "target"}
        if trail:
            new_stop = trail(day, k, direction, entry_px, stop)
            if new_stop is not None:
                stop = max(stop, new_stop) if direction > 0 else min(stop, new_stop)
    px = day.c[-1]
    return {"pts": direction * (px - entry_px), "mae": mae, "exit": "cierre"}


def sim(day, spec, target_override=None):
    """spec: dict(i, dir, px, stop_pts, target_pts, exit_min, trail). Devuelve trade con pts/mae/stop_pts."""
    if "fn" in spec:
        r = spec["fn"]()
        r["stop_pts"] = spec["stop_pts"]; r["date"] = day.date; r["dir"] = spec["dir"]
        return r
    tp = target_override if target_override is not None else spec.get("target_pts")
    r = run_trade(day, spec["i"], spec["dir"], spec["px"], spec["stop_pts"], tp, spec.get("exit_min", 380), spec.get("trail"))
    r["stop_pts"] = spec["stop_pts"]; r["date"] = day.date; r["dir"] = spec["dir"]
    return r


def dollars(pts, contracts):
    return pts * PT_VALUE * contracts - contracts * (COST_RT + SLIP_PTS * PT_VALUE)


def contracts_for(risk_usd, stop_pts):
    if stop_pts <= 0:
        return 0
    return max(0, min(MAX_CONTRACTS, int(risk_usd // (stop_pts * PT_VALUE))))


# ------------------------------------------------------------------ metricas capital propio (riesgo fijo)
def metrics(trades, risk_usd=500):
    """trades: lista de dict con pts, mae, stop_pts, date. Riesgo fijo por trade."""
    pnl, eq, peak, dd, R = [], 0.0, 0.0, 0.0, []
    for t in trades:
        n = contracts_for(risk_usd, t["stop_pts"])
        if n == 0:
            continue
        p = dollars(t["pts"], n)
        pnl.append(p); R.append(p / risk_usd)
        eq += p; peak = max(peak, eq); dd = max(dd, peak - eq)
    if not pnl:
        return None
    wins = [p for p in pnl if p > 0]; losses = [-p for p in pnl if p <= 0]
    pf = sum(wins) / sum(losses) if losses and sum(losses) > 0 else float("inf")
    return {"n": len(pnl), "wr": len(wins) / len(pnl), "avgR": sum(R) / len(R), "pf": pf,
            "total": eq, "maxdd": dd}


# ------------------------------------------------------------------ simulador Apex 25K EOD
EVAL = {"start": 25000, "target": 1500, "dd": 1000, "daily": 500, "max_days": 21}
PA = {"start": 25000, "dd": 1000, "lock": 25100, "safety": 26100, "min_payout": 500, "max_payout": 1000,
      "payouts": 6, "days_100": 5, "consistency": 0.5}


def day_results(days, strategy, risk_usd, fixed_target_usd=None):
    """P&L por dia (lista alineada con days): (pnl, peor_equity_intradia) o None si no opera ese dia."""
    out = []
    for i in range(len(days)):
        specs = strategy(days, i)
        if not specs:
            out.append(None); continue
        pnl, worst, traded = 0.0, 0.0, False
        for spec in specs:
            n = contracts_for(risk_usd, spec["stop_pts"])
            if n == 0:
                continue
            tp = None
            if fixed_target_usd:   # modo examen: objetivo fijo en dolares netos
                tp = (fixed_target_usd + n * (COST_RT + SLIP_PTS * PT_VALUE)) / (n * PT_VALUE)
            t = sim(days[i], spec, tp)
            traded = True
            worst = min(worst, pnl - t["mae"] * PT_VALUE * n - n * (COST_RT + SLIP_PTS * PT_VALUE))
            pnl += dollars(t["pts"], n)
            if pnl <= -EVAL["daily"]:
                break
        out.append((pnl, worst) if traded else None)
    return out


def sim_eval(res, start):
    """Examen desde el dia start. Devuelve ('pass'|'fail'|'timeout', dias_operados, dia_fin)."""
    bal, peak = EVAL["start"], EVAL["start"]
    tdays = 0
    for k in range(start, len(res)):
        tdays += 1
        r = res[k]
        if r:
            pnl, worst = r
            thr = peak - EVAL["dd"]
            if bal + worst <= thr:
                return "fail", tdays, k
            bal += pnl
            if bal <= thr:
                return "fail", tdays, k
            if bal >= EVAL["start"] + EVAL["target"]:
                return "pass", tdays, k
            peak = max(peak, bal)
        if tdays >= EVAL["max_days"]:
            return "timeout", tdays, k
    return "timeout", tdays, len(res) - 1


def sim_pa(res, start, max_days=120):
    """Cuenta fondeada desde start. Devuelve (retiros_usd, n_retiros, quemada?)."""
    bal, peak, thr = PA["start"], PA["start"], PA["start"] - PA["dd"]
    paid, n_paid, good_days, since = 0.0, 0, 0, []
    for k in range(start, min(len(res), start + max_days)):
        r = res[k]
        if not r:
            continue
        pnl, worst = r
        if bal + worst <= thr:
            return paid, n_paid, True
        bal += pnl
        if bal <= thr:
            return paid, n_paid, True
        peak = max(peak, bal)
        thr = min(PA["lock"], max(thr, peak - PA["dd"]))
        since.append(pnl)
        if pnl >= 100:
            good_days += 1
        tot = sum(since)
        if (good_days >= PA["days_100"] and tot > 0 and max(since) <= PA["consistency"] * tot
                and bal - PA["safety"] >= PA["min_payout"]):
            amt = min(PA["max_payout"], bal - PA["safety"])
            paid += amt; n_paid += 1; bal -= amt
            good_days, since = 0, []
            if n_paid >= PA["payouts"]:
                return paid, n_paid, False
    return paid, n_paid, False


def prop_report(days, strategy, risk_eval, fixed_target, risk_pa, lo, hi):
    """Examen + PA para todos los dias de inicio entre lo y hi (indices). Devuelve dict."""
    res_e = day_results(days, strategy, risk_eval, fixed_target)
    res_p = day_results(days, strategy, risk_pa)
    passes = fails = tos = 0
    dpass, payouts, npay, burned = [], [], [], 0
    for s in range(lo, hi):
        st, nd, end = sim_eval(res_e, s)
        if st == "pass":
            passes += 1; dpass.append(nd)
            p, n, b = sim_pa(res_p, end + 1)
            payouts.append(p); npay.append(n); burned += b
        elif st == "fail":
            fails += 1
        else:
            tos += 1
    tot = passes + fails + tos
    return {"pass_rate": passes / tot if tot else 0, "fail": fails / tot if tot else 0, "timeout": tos / tot if tot else 0,
            "days_to_pass": sorted(dpass)[len(dpass) // 2] if dpass else None,
            "pa_payout_avg": sum(payouts) / len(payouts) if payouts else 0,
            "pa_npay_avg": sum(npay) / len(npay) if npay else 0,
            "pa_burn": burned / len(payouts) if payouts else 0,
            "exp_per_attempt": (passes / tot) * (sum(payouts) / len(payouts)) if tot and payouts else 0}
