# Arma el archivo "uruguay.json" para "Mi Uruguay" (niebla de guerra y
# rutas nacionales) en la app Nutrak, con datos de OpenStreetMap:
#
#   - Los 19 departamentos (contorno simplificado) y cuántos cuadraditos
#     de 2 km tiene cada uno.
#   - Las rutas nacionales: un punto cada 500 m de cada ruta.
#   - Argentina y Brasil (contorno simplificado, de Natural Earth) para los
#     sellos de países del Pasaporte motero.
#
# Estas cosas casi no cambian: se rehace una vez por mes (el día 1), o
# cuando se corre a mano el workflow, o si todavía no existe.
#
# IMPORTANTE: la cuadrícula tiene que calcularse IGUAL que en la app
# (lib/servicios/uruguay.dart): mismas constantes LAT0, KY y CELDA.

import datetime
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

from shapely.geometry import LineString, Point, Polygon, shape
from shapely.ops import polygonize, unary_union
from shapely.prepared import prep

SERVIDORES = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
SALIDA = "salida"
ARCHIVO = os.path.join(SALIDA, "uruguay.json")

# Cuadrícula (igual que en la app)
CELDA = 2000.0  # metros
LAT0 = -32.5  # latitud de referencia de Uruguay
KY = 110574.0  # metros por grado de latitud
KX = 111320.0 * math.cos(math.radians(LAT0))  # metros por grado de longitud

PAISES_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
              "master/geojson/ne_50m_admin_0_countries.geojson")
PAISES = {"ARG": ("AR", "Argentina"), "BRA": ("BR", "Brasil")}

SIMPLIFICAR = 0.004  # grados (~400 m): contorno liviano pero fiel
PASO_RUTA = 500.0  # metros entre puntos de una ruta


def log(texto):
    print(texto, flush=True)


def hay_que_hacerlo():
    if os.environ.get("FORZAR") == "1":
        return True
    if not os.path.exists(ARCHIVO):
        return True
    return datetime.date.today().day == 1


def overpass(consulta, intentos=3):
    datos = urllib.parse.urlencode({"data": consulta}).encode()
    for vuelta in range(intentos):
        for servidor in SERVIDORES:
            try:
                log(f"  pidiendo a {servidor}…")
                pedido = urllib.request.Request(
                    servidor, data=datos,
                    headers={"User-Agent": "Nutrak/1.0 (datos de Mi Uruguay)"})
                with urllib.request.urlopen(pedido, timeout=600) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:  # noqa: BLE001
                log(f"  falló: {e}")
        log("  esperando 60 s y probando de nuevo…")
        time.sleep(60)
    raise RuntimeError("Ningún servidor respondió")


def celda_de(lat, lng):
    return (math.floor(lng * KX / CELDA), math.floor(lat * KY / CELDA))


def centro_de(i, j):
    return ((j + 0.5) * CELDA / KY, (i + 0.5) * CELDA / KX)  # (lat, lng)


# ---------- Departamentos ----------

def bajar_departamentos():
    log("Departamentos…")
    datos = overpass("""
[out:json][timeout:500];
area["ISO3166-1"="UY"][admin_level=2]->.uy;
rel(area.uy)["boundary"="administrative"]["admin_level"="4"];
out geom;
""")
    departamentos = []
    for rel in datos.get("elements", []):
        nombre = rel.get("tags", {}).get("name", "").replace("Departamento de ", "")
        lineas = []
        for m in rel.get("members", []):
            if m.get("type") != "way" or m.get("role") not in ("outer", ""):
                continue
            geo = m.get("geometry") or []
            if len(geo) >= 2:
                lineas.append(LineString([(p["lon"], p["lat"]) for p in geo]))
        poligonos = list(polygonize(unary_union(lineas)))
        if not poligonos or not nombre:
            log(f"  (sin contorno: {nombre})")
            continue
        forma = unary_union(poligonos).simplify(SIMPLIFICAR, preserve_topology=True)
        departamentos.append((nombre, forma))
        log(f"  {nombre}")
    if len(departamentos) < 15:
        raise RuntimeError(f"Llegaron pocos departamentos ({len(departamentos)})")
    return departamentos


def contar_celdas(departamentos):
    log("Contando cuadraditos de 2 km…")
    todo = unary_union([f for _, f in departamentos])
    oeste, sur, este, norte = todo.bounds
    i0, j0 = celda_de(sur, oeste)
    i1, j1 = celda_de(norte, este)
    preparados = [(n, prep(f)) for n, f in departamentos]
    por_depto = {n: 0 for n, _ in departamentos}
    total = 0
    for i in range(i0, i1 + 1):
        for j in range(j0, j1 + 1):
            lat, lng = centro_de(i, j)
            punto = Point(lng, lat)
            for n, f in preparados:
                if f.contains(punto):
                    por_depto[n] += 1
                    total += 1
                    break
    log(f"  {total} cuadraditos en total")
    return total, por_depto


def anillos(forma):
    # Solo los contornos de afuera (las islas cuentan como otro anillo)
    partes = list(forma.geoms) if forma.geom_type == "MultiPolygon" else [forma]
    salida = []
    for p in partes:
        if isinstance(p, Polygon) and p.area > 0.0005:
            salida.append([[round(y, 4), round(x, 4)] for x, y in p.exterior.coords])
    return salida


# ---------- Países vecinos ----------

def bajar_paises():
    log("Países vecinos…")
    pedido = urllib.request.Request(PAISES_URL, headers={"User-Agent": "Nutrak/1.0"})
    with urllib.request.urlopen(pedido, timeout=300) as r:
        datos = json.loads(r.read().decode("utf-8"))
    salida = []
    for f in datos.get("features", []):
        props = f.get("properties", {})
        clave = props.get("ADM0_A3") or props.get("ISO_A3")
        if clave not in PAISES:
            continue
        codigo, nombre = PAISES[clave]
        forma = shape(f["geometry"]).simplify(0.01, preserve_topology=True)
        partes = list(forma.geoms) if forma.geom_type == "MultiPolygon" else [forma]
        anillos = [
            [[round(y, 3), round(x, 3)] for x, y in p.exterior.coords]
            for p in partes
            if p.area > 0.05
        ]
        salida.append({"codigo": codigo, "nombre": nombre, "anillos": anillos})
        log(f"  {nombre}: {sum(len(a) for a in anillos)} puntos")
    if len(salida) < 2:
        raise RuntimeError("No llegaron Argentina y Brasil")
    return salida


# ---------- Rutas nacionales ----------

def bajar_rutas(todo):
    log("Rutas nacionales…")
    datos = overpass("""
[out:json][timeout:500];
area["ISO3166-1"="UY"][admin_level=2]->.uy;
way(area.uy)["highway"~"^(motorway|trunk|primary|secondary)$"]["ref"];
out geom;
""")
    rutas = {}
    dentro = prep(todo.buffer(0.01))
    for w in datos.get("elements", []):
        refs = w.get("tags", {}).get("ref", "")
        geo = w.get("geometry") or []
        if len(geo) < 2:
            continue
        for ref in refs.replace(",", ";").split(";"):
            ref = ref.strip().upper().replace("RUTA ", "")
            if not (ref.isdigit() and 1 <= int(ref) <= 109) and ref != "IB":
                continue
            rutas.setdefault(ref, []).append([(p["lat"], p["lon"]) for p in geo])

    salida = []
    for ref, tramos in rutas.items():
        puntos = []
        vistos = set()
        for tramo in tramos:
            for (la1, lo1), (la2, lo2) in zip(tramo, tramo[1:]):
                largo = math.hypot((la2 - la1) * KY, (lo2 - lo1) * KX)
                pasos = max(1, int(largo // PASO_RUTA))
                for k in range(pasos):
                    la = la1 + (la2 - la1) * k / pasos
                    lo = lo1 + (lo2 - lo1) * k / pasos
                    # Un punto por cuadradito de 400 m (sin repetir carriles dobles)
                    clave = (round(la * KY / 400), round(lo * KX / 400))
                    if clave in vistos or not dentro.contains(Point(lo, la)):
                        continue
                    vistos.add(clave)
                    puntos.append([round(la, 4), round(lo, 4)])
        if len(puntos) >= 4:  # más de 2 km
            salida.append({"ref": ref, "puntos": puntos})
    salida.sort(key=lambda r: (r["ref"] != "IB", int(r["ref"]) if r["ref"].isdigit() else 0))
    log(f"  {len(salida)} rutas")
    return salida


def main():
    if not hay_que_hacerlo():
        log("Mi Uruguay: no toca rehacerlo hoy (se rehace el día 1 de cada mes)")
        return
    try:
        departamentos = bajar_departamentos()
        total, por_depto = contar_celdas(departamentos)
        todo = unary_union([f for _, f in departamentos])
        rutas = bajar_rutas(todo)
        try:
            paises = bajar_paises()
        except Exception as e:  # noqa: BLE001
            log(f"  (sin países vecinos: {e})")
            paises = []
    except Exception as e:  # noqa: BLE001
        log(f"No se pudo armar Mi Uruguay ({e}). Queda el archivo anterior.")
        return
    datos = {
        "version": datetime.date.today().isoformat(),
        "celda": CELDA,
        "lat0": LAT0,
        "total": total,
        "departamentos": [
            {"nombre": n, "celdas": por_depto[n], "anillos": anillos(f)}
            for n, f in sorted(departamentos)
        ],
        "rutas": rutas,
        "paises": paises,
    }
    os.makedirs(SALIDA, exist_ok=True)
    with open(ARCHIVO, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    log(f"Listo: {ARCHIVO} ({os.path.getsize(ARCHIVO) // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
