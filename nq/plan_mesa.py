#!/usr/bin/env python3
"""Plan "mesa" (video isaacm.trading) adaptado a Apex 25K con la senal del empujon NY:
examen un disparo; PA: tiros grandes (riesgo 500 -> +2000) hasta asegurar colchon, despues dias chicos.
Compara reglas Apex (consistencia 50 %, 5 dias >= $100) contra reglas sin consistencia."""
import datetime as dt
import nqlib as L
import strategies as S

days = L.load("data/nq_*.csv.gz")
cut = next(i for i, d in enumerate(days) if d.date >= dt.date(2022, 1, 1))
st = S.empujon
eval_res = L.day_results(days, st, 450, 1550)


def pa_plan(big, small, start, consistency, max_days=120):
    bal, peak, thr = 25000.0, 25000.0, 24000.0
    paid = n = good = 0; since = []; mode_big = True
    for k in range(start, min(len(days), start + max_days)):
        r = (big if mode_big else small)[k]
        if not r:
            continue
        pnl, worst = r
        if bal + worst <= thr:
            return paid, n, True
        bal += pnl
        if bal <= thr:
            return paid, n, True
        peak = max(peak, bal); thr = min(25100.0, max(thr, peak - 1000))
        if mode_big and bal >= 26600:
            mode_big = False
        since.append(pnl); good += pnl >= 100
        tot = sum(since)
        ok_cons = consistency is None or (tot > 0 and max(since) <= consistency * tot)
        if good >= 5 and tot > 0 and ok_cons and bal - 26100 >= 500:
            amt = min(1000, bal - 26100); paid += amt; n += 1; bal -= amt; good = 0; since = []
            if n >= 6:
                break
    return paid, n, False


def run(label, big_risk, big_tgt, small_risk, small_tgt):
    big = L.day_results(days, st, big_risk, big_tgt)
    small = L.day_results(days, st, small_risk, small_tgt)
    for per, lo, hi in (("2014-21", 20, cut - 140), ("2022-26", cut, len(days) - 140)):
        out = []
        for cons in (0.5, None):
            tot = cnt = passed = burns = npay = 0
            for s in range(lo, hi, 2):
                stt, _, end = L.sim_eval(eval_res, s); cnt += 1
                if stt != "pass":
                    continue
                passed += 1
                p, n, b = pa_plan(big, small, end + 1, cons)
                tot += p; npay += n; burns += b
            avg = tot / passed if passed else 0
            out.append(f"{'Apex (consist. 50%)' if cons else 'sin consistencia'}: retiro ${avg:,.0f}/cuenta, {npay/max(passed,1):.2f} retiros, quema {burns/max(passed,1)*100:.0f}%, EV/intento ${passed/cnt*avg:,.0f}")
        print(f"{label:<44} {per} (pasa {passed/cnt*100:.0f}%): " + " | ".join(out))


print("Empujon NY, examen un disparo ($450 -> +$1550)\n")
run("PA plano: riesgo $200, objetivo $300", 200, 300, 200, 300)
run("PA mesa: tiro $500->+$2000, luego $150->+$110", 500, 2000, 150, 110)
run("PA mesa: tiro $500->+$2000, luego $75->+$110", 500, 2000, 75, 110)
run("PA mesa: tiro $450->+$1550, luego $150->+$150", 450, 1550, 150, 150)
