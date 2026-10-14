# Baja de OpenStreetMap las estaciones de servicio, talleres de motos,
# talleres mecánicos y gomerías de Uruguay para la app Nutrak.
# Lo corre GitHub todos los días (ver .github/workflows/actualizar.yml).
#
# Deja en la carpeta "salida": uy.json e indice.json (con la versión).
# Si OpenStreetMap no responde, se deja la lista anterior.
#
# (Está preparado para sumar otros países pidiéndolos por "cuadrados" del
# mapa, por si algún día se quiere ampliar.)

import datetime
import hashlib
import json
import os
import sys
import time
import urllib.parse
import urllib.request

SERVIDORES = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]
SALIDA = "salida"

FILTROS_TODO = [
    '["amenity"="fuel"]',
    '["shop"="motorcycle"]',
    '["shop"="motorcycle_repair"]',
    '["craft"="motorcycle_repair"]',
    '["shop"="tyres"]',
    '["shop"="car_repair"]',
]
FILTROS_MOTERO = FILTROS_TODO[:-1]  # sin talleres de autos

# Rectángulo (sur, oeste, norte, este) que cubre cada país
# Por ahora solo Uruguay. Argentina y Brasil se buscan en vivo desde la app.
# (Para sumarlos más adelante: agregarlos acá con una "caja" sur, oeste,
# norte, este, y los filtros que se quieran.)
PAISES = {
    "uy": {"area": '["ISO3166-1"="UY"][admin_level=2]', "filtros": FILTROS_TODO, "diario": True},
}
TAMANO_CUADRADO = 4.0


def log(texto):
    print(texto, flush=True)


# 0 = estación, 1 = taller de motos, 2 = taller mecánico, 3 = gomería
def tipo_de(tags):
    if tags.get("amenity") == "fuel":
        return 0
    shop = tags.get("shop")
    if shop == "tyres":
        return 3
    if shop == "car_repair":
        return 2
    return 1


def consultar(consulta, intentos_por_servidor=1):
    """Prueba los servidores; devuelve los elementos o None si todos fallan."""
    datos = urllib.parse.urlencode({"data": consulta}).encode()
    for _ in range(intentos_por_servidor):
        for servidor in SERVIDORES:
            try:
                pedido = urllib.request.Request(
                    servidor, data=datos,
                    headers={"User-Agent": "Nutrak/1.0 (lista de lugares para motoviajeros)"},
                )
                with urllib.request.urlopen(pedido, timeout=200) as r:
                    respuesta = json.loads(r.read().decode("utf-8"))
                nota = str(respuesta.get("remark", ""))
                if "timed out" in nota or "out of memory" in nota:
                    raise RuntimeError(nota[:80])
                if isinstance(respuesta.get("elements"), list):
                    return respuesta["elements"]
            except Exception as e:
                log(f"    {servidor.split('/')[2]}: {e}")
            time.sleep(5)
    return None


def consulta_de(lugar, filtros, segundos):
    texto = f"[out:json][timeout:{segundos}];\n"
    if lugar.startswith("area"):
        texto += lugar + "->.z;\n(\n" + "".join(f"  nwr{f}(area.z);\n" for f in filtros)
    else:
        texto += "(\n" + "".join(f"  nwr{f}({lugar});\n" for f in filtros)
    return texto + ");\nout center tags;\n"


def cuadrados(caja, tamano):
    sur, oeste, norte, este = caja
    lat = sur
    while lat < norte:
        lon = oeste
        while lon < este:
            yield (lat, lon, min(lat + tamano, norte), min(lon + tamano, este))
            lon += tamano
        lat += tamano


def bajar_cuadrado(c, filtros, nivel=0):
    """Devuelve (elementos, cuadrados_que_fallaron)."""
    caja = f"{c[0]},{c[1]},{c[2]},{c[3]}"
    elementos = consultar(consulta_de(caja, filtros, 120))
    if elementos is not None:
        return elementos, []
    if nivel >= 2:
        return [], [c]
    # Muy pesado: lo partimos en 4
    log(f"  partiendo {caja} en 4")
    medio_lat, medio_lon = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
    todos, fallas = [], []
    for sub in [(c[0], c[1], medio_lat, medio_lon), (c[0], medio_lon, medio_lat, c[3]),
                (medio_lat, c[1], c[2], medio_lon), (medio_lat, medio_lon, c[2], c[3])]:
        e, f = bajar_cuadrado(sub, filtros, nivel + 1)
        todos += e
        fallas += f
        time.sleep(3)
    return todos, fallas


def a_fila(el):
    lat = el.get("lat", el.get("center", {}).get("lat"))
    lon = el.get("lon", el.get("center", {}).get("lon"))
    if lat is None or lon is None:
        return None
    tags = el.get("tags", {})
    ident = f"{el['type']}/{el['id']}"
    return [ident, tipo_de(tags), round(lat, 5), round(lon, 5), tags.get("name", ""),
            tags.get("brand") or tags.get("operator") or "", tags.get("opening_hours", "")]


def bajar_pais(codigo, config, anteriores):
    lugares = {}
    if "area" in config:
        elementos = consultar(consulta_de("area" + config["area"], config["filtros"], 240), 3)
        if not elementos:
            return None
        for el in elementos:
            fila = a_fila(el)
            if fila:
                lugares[fila[0]] = fila
        return sorted(lugares.values(), key=lambda p: p[0])

    lista = list(cuadrados(config["caja"], TAMANO_CUADRADO))
    fallas = []
    for i, c in enumerate(lista, 1):
        elementos, f = bajar_cuadrado(c, config["filtros"])
        fallas += f
        for el in elementos:
            fila = a_fila(el)
            if fila:
                lugares[fila[0]] = fila
        log(f"  {codigo} cuadrado {i}/{len(lista)}: {len(elementos)} lugares (total {len(lugares)})")
        time.sleep(3)

    # Zonas que fallaron: usamos lo que había antes ahí
    for f in fallas:
        log(f"  {codigo}: falló {f}, se usa lo anterior para esa zona")
        for p in anteriores:
            if f[0] <= p[2] <= f[2] and f[1] <= p[3] <= f[3]:
                lugares.setdefault(p[0], p)
    if not lugares:
        return None
    return sorted(lugares.values(), key=lambda p: p[0])


def main():
    os.makedirs(SALIDA, exist_ok=True)
    ruta_indice = os.path.join(SALIDA, "indice.json")
    indice = {}
    if os.path.exists(ruta_indice):
        with open(ruta_indice, encoding="utf-8") as f:
            indice = json.load(f)

    es_lunes = datetime.datetime.now(datetime.timezone.utc).weekday() == 0
    forzar = os.environ.get("FORZAR") == "1"

    for codigo, config in PAISES.items():
        archivo = os.path.join(SALIDA, f"{codigo}.json")
        toca = config["diario"] or es_lunes or forzar or not os.path.exists(archivo)
        if not toca:
            log(f"{codigo}: hoy no toca")
            continue
        anteriores = []
        if os.path.exists(archivo):
            with open(archivo, encoding="utf-8") as f:
                anteriores = json.load(f).get("p", [])
        log(f"{codigo}: bajando…")
        lugares = bajar_pais(codigo, config, anteriores)
        if lugares is None:
            log(f"{codigo}: falló, se deja la lista anterior")
            continue
        huella = hashlib.sha1(json.dumps(lugares, ensure_ascii=False).encode()).hexdigest()[:12]
        cuantos = [sum(1 for p in lugares if p[1] == t) for t in range(4)]
        log(f"{codigo}: {len(lugares)} lugares (estaciones {cuantos[0]}, talleres de motos "
            f"{cuantos[1]}, talleres {cuantos[2]}, gomerías {cuantos[3]}) versión {huella}")
        if indice.get(codigo) == huella:
            continue
        with open(archivo, "w", encoding="utf-8") as f:
            json.dump({"v": huella, "p": lugares}, f, ensure_ascii=False, separators=(",", ":"))
        indice[codigo] = huella

    with open(ruta_indice, "w", encoding="utf-8") as f:
        json.dump(indice, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
