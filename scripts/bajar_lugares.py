# Baja de OpenStreetMap las estaciones de servicio, talleres y gomerías
# de Uruguay, Argentina y Brasil para la app Nutrak.
#
# - Uruguay: todo (estaciones, talleres de motos, talleres mecánicos y
#   gomerías). Se actualiza todos los días.
# - Argentina y Brasil: lo motero (estaciones, talleres de motos y
#   gomerías). Se actualizan los lunes (cambian poco y son grandes).
#   Se piden provincia por provincia / estado por estado, porque un pedido
#   por todo el país hace fallar al servidor.
#
# Deja en la carpeta "salida": uy.json, ar.json, br.json e indice.json
# (con la versión de cada uno). Si un país falla, se deja el anterior.

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

ESTADOS_BR = "AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO".split()
PROVINCIAS_AR = "C B K H U X W E P Y L F M N Q R A J D Z S G V T".split()

PAISES = {
    "uy": {"zonas": ['["ISO3166-1"="UY"][admin_level=2]'], "filtros": FILTROS_TODO, "diario": True},
    "ar": {"zonas": [f'["ISO3166-2"="AR-{p}"]' for p in PROVINCIAS_AR], "filtros": FILTROS_MOTERO, "diario": False},
    "br": {"zonas": [f'["ISO3166-2"="BR-{e}"]' for e in ESTADOS_BR], "filtros": FILTROS_MOTERO, "diario": False},
}


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


def pedir(zona, filtros):
    consulta = "[out:json][timeout:300];\narea" + zona + "->.z;\n(\n"
    consulta += "".join(f"  nwr{f}(area.z);\n" for f in filtros)
    consulta += ");\nout center tags;\n"
    datos = urllib.parse.urlencode({"data": consulta}).encode()
    for intento in range(3):
        for servidor in SERVIDORES:
            try:
                pedido = urllib.request.Request(
                    servidor, data=datos,
                    headers={"User-Agent": "Nutrak/1.0 (lista de lugares para motoviajeros)"},
                )
                with urllib.request.urlopen(pedido, timeout=360) as r:
                    respuesta = json.loads(r.read().decode("utf-8"))
                nota = str(respuesta.get("remark", ""))
                if "timed out" in nota or "out of memory" in nota:
                    raise RuntimeError(nota[:100])
                if isinstance(respuesta.get("elements"), list):
                    return respuesta["elements"]
            except Exception as e:
                print(f"  {zona} en {servidor}: {e}", file=sys.stderr)
            time.sleep(10)
        time.sleep(60)
    return None


def bajar_pais(codigo, config):
    lugares = {}
    for zona in config["zonas"]:
        elementos = pedir(zona, config["filtros"])
        if elementos is None:
            print(f"{codigo}: falló {zona}, se deja la lista anterior")
            return None
        if not elementos and codigo == "uy":
            return None  # Uruguay vacío = algo anduvo mal
        for el in elementos:
            lat = el.get("lat", el.get("center", {}).get("lat"))
            lon = el.get("lon", el.get("center", {}).get("lon"))
            if lat is None or lon is None:
                continue
            tags = el.get("tags", {})
            ident = f"{el['type']}/{el['id']}"
            lugares[ident] = [
                ident,
                tipo_de(tags),
                round(lat, 5),
                round(lon, 5),
                tags.get("name", ""),
                tags.get("brand") or tags.get("operator") or "",
                tags.get("opening_hours", ""),
            ]
        time.sleep(5)  # para no saturar el servicio gratuito
    return sorted(lugares.values(), key=lambda p: p[0])


def main():
    os.makedirs(SALIDA, exist_ok=True)
    ruta_indice = os.path.join(SALIDA, "indice.json")
    indice = {}
    if os.path.exists(ruta_indice):
        with open(ruta_indice, encoding="utf-8") as f:
            indice = json.load(f)

    es_lunes = datetime.datetime.utcnow().weekday() == 0
    forzar = os.environ.get("FORZAR") == "1"

    for codigo, config in PAISES.items():
        archivo = os.path.join(SALIDA, f"{codigo}.json")
        toca = config["diario"] or es_lunes or forzar or not os.path.exists(archivo)
        if not toca:
            print(f"{codigo}: hoy no toca")
            continue
        print(f"{codigo}: bajando {len(config['zonas'])} zona(s)…")
        lugares = bajar_pais(codigo, config)
        if lugares is None:
            continue
        huella = hashlib.sha1(json.dumps(lugares, ensure_ascii=False).encode()).hexdigest()[:12]
        cuantos = [sum(1 for p in lugares if p[1] == t) for t in range(4)]
        print(f"{codigo}: {len(lugares)} lugares (estaciones {cuantos[0]}, talleres de motos "
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
