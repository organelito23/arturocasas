"""Completa las velas de 1 minuto del año en curso en la rama data (las usa CALDER con "Actualizar año en curso").

Por cada instrumento lee la ultima vela guardada, baja de Dukascopy (dukascopy-node, precio bid) desde ese dia
hasta mañana, junta sin repetir y vuelve a comprimir. Uso: python3 velas/actualizar.py <carpeta de la rama data>
"""
import csv
import datetime as dt
import gzip
import io
import os
import subprocess
import sys

INSTRUMENTOS = [  # instrumento de Dukascopy, carpeta, prefijo (los nombres que espera CALDER)
    ("xauusd", "xauusd", "xauusd"),
    ("usatechidxusd", "nasdaq", "nq"),
    ("btcusd", "btcusd", "btcusd"),
]
CABECERA = "timestamp,open,high,low,close\n"


def leer(ruta):
    filas = {}
    if os.path.exists(ruta):
        with gzip.open(ruta, "rt") as f:
            next(f, None)
            for linea in f:
                p = linea.rstrip("\n").split(",")
                if len(p) >= 5 and p[0]:
                    filas[int(float(p[0]))] = ",".join(p[1:5])
    return filas


def bajar(inst, desde, hasta, tmp):
    os.makedirs(tmp, exist_ok=True)
    nombre = f"{inst}_{desde}"
    cmd = ["npx", "-y", "dukascopy-node", "-i", inst, "-from", desde, "-to", hasta, "-t", "m1", "-f", "csv",
           "--retries", "5", "--batch-size", "8", "--batch-pause", "1000", "-dir", tmp, "-fn", nombre]
    subprocess.run(cmd, check=True, timeout=1200)
    filas = {}
    with open(os.path.join(tmp, nombre + ".csv")) as f:
        for r in csv.reader(f):
            if len(r) >= 5 and r[0].replace(".", "", 1).isdigit():
                filas[int(float(r[0]))] = ",".join(r[1:5])
    return filas


def actualizar(raiz, inst, carpeta, prefijo, anio, hoy):
    ruta = os.path.join(raiz, carpeta, f"{prefijo}_{anio}.csv.gz")
    filas = leer(ruta)
    ini = dt.datetime(anio, 1, 1, tzinfo=dt.timezone.utc)
    fin = dt.datetime(anio + 1, 1, 1, tzinfo=dt.timezone.utc)
    ultima = max(filas) if filas else None
    desde = (dt.datetime.fromtimestamp(ultima / 1000, dt.timezone.utc) if ultima else ini).date()
    hasta = min(hoy + dt.timedelta(days=1), fin.date())
    if desde >= hasta:
        return 0
    nuevas = bajar(inst, desde.isoformat(), hasta.isoformat(), os.path.join(raiz, "..", "tmp_velas"))
    antes = len(filas)
    lo, hi = int(ini.timestamp() * 1000), int(fin.timestamp() * 1000)
    filas.update({k: v for k, v in nuevas.items() if lo <= k < hi})
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    buf = io.StringIO()
    buf.write(CABECERA)
    for k in sorted(filas):
        buf.write(f"{k},{filas[k]}\n")
    with gzip.open(ruta, "wt", compresslevel=9) as f:
        f.write(buf.getvalue())
    return len(filas) - antes


def main():
    raiz = sys.argv[1]
    hoy = dt.datetime.now(dt.timezone.utc).date()
    anios = [hoy.year] + ([hoy.year - 1] if hoy.timetuple().tm_yday <= 3 else [])   # en año nuevo, cerrar el anterior
    fallos = 0
    for inst, carpeta, prefijo in INSTRUMENTOS:
        for anio in anios:
            try:
                n = actualizar(raiz, inst, carpeta, prefijo, anio, hoy)
                print(f"{carpeta}/{prefijo}_{anio}: +{n} velas")
            except Exception as e:  # un instrumento que falla no frena a los demas
                fallos += 1
                print(f"{carpeta}/{prefijo}_{anio}: ERROR {e}")
    sys.exit(1 if fallos == len(INSTRUMENTOS) * len(anios) else 0)


if __name__ == "__main__":
    main()
