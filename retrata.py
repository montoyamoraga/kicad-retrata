#!/usr/bin/env python3
"""kicad-retrata: genera capturas SVG del esquemático y de la placa de cada
módulo de un repositorio KiCad, y regenera la tabla "Bill of materials" en
su documento Markdown.

Uso: python3 retrata.py [--configuracion kicad-retrata.yml] [--rutas ARCHIVO]

Se corre desde la raíz del repositorio: todas las rutas del archivo de
configuración son relativas al directorio actual.

Requiere kicad-cli (KiCad 10) en el PATH, o la variable de entorno KICAD_CLI
con el comando completo (por ejemplo, envuelto en "docker run ... kicad-cli"
como en action.yml).
"""
import argparse
import csv
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

# lib_id ("Biblioteca:Nombre") -> descripción en español, para la columna
# Descripción de la tabla. Cada repositorio puede agregar o reemplazar
# entradas con la clave "descripciones" de su archivo de configuración. Si
# aparece un lib_id que no está en ninguno de los dos, se avisa y se usa el
# lib_id tal cual como descripción de respaldo.
DESCRIPCIONES_BASE = {
    "Device:R": "Resistencia",
    "Device:R_Small": "Resistencia",
    "Device:R_Potentiometer": "Potenciómetro",
    "Device:R_Potentiometer_Dual": "Potenciómetro doble",
    "Device:C": "Capacitor cerámico",
    "Device:C_Small": "Capacitor cerámico",
    "Device:C_Polarized": "Capacitor electrolítico",
    "Device:C_Polarized_Small": "Capacitor electrolítico",
    "Device:L": "Inductor",
    "Device:D": "Diodo",
    "Device:D_Schottky": "Diodo Schottky",
    "Device:D_Zener": "Diodo Zener",
    "Device:LED": "LED",
    "Device:Crystal": "Cristal",
    "Device:Fuse": "Fusible",
    "Device:Polyfuse": "Fusible reseteable (PTC)",
    "Transistor_FET:Q_PMOS_GSD": "Transistor MOSFET canal P",
    "Transistor_FET:Q_NMOS_GSD": "Transistor MOSFET canal N",
    "Transistor_BJT:BC547": "Transistor NPN",
    "Transistor_BJT:BC557": "Transistor PNP",
    "Amplifier_Operational:TL072": "Amplificador operacional dual",
    "Amplifier_Operational:TL074": "Amplificador operacional cuádruple",
    "Amplifier_Audio:PAM8403D": "Amplificador de audio clase D",
    "Timer:NE555P": "Temporizador 555",
    "Timer:NA556": "Temporizador doble 556",
    "Regulator_Linear:L7805": "Regulador de voltaje lineal +5V",
    "Regulator_Linear:L7812": "Regulador de voltaje lineal +12V",
    "Regulator_Linear:L7912": "Regulador de voltaje lineal -12V",
    "Regulator_Linear:AMS1117-3.3": "Regulador de voltaje lineal +3.3V",
    "Connector:Barrel_Jack": "Jack de alimentación (barrel)",
    "Connector:Barrel_Jack_Switch": "Jack de alimentación (barrel)",
    "Connector:Conn_01x04_Pin": "Conector de alimentación (4 pines)",
    "Connector_Audio:AudioJack2": "Jack de audio 3.5mm",
    "Connector_Audio:AudioJack2_SwitchT": "Jack de audio 3.5mm",
    "Connector_Generic:Conn_02x05_Odd_Even": "Header de alimentación Eurorack (10 pines)",
    "Connector_Generic:Conn_02x08_Odd_Even": "Header de alimentación Eurorack (16 pines)",
    "Switch:SW_Push": "Pulsador",
    "Switch:SW_SPDT": "Interruptor SPDT",
}

CAPAS_PLACA_BASE = ["F.Cu", "F.Mask", "F.Silkscreen", "Edge.Cuts"]

INICIO_MARCA = "<!-- BOM_TABLE_START -->"
FIN_MARCA = "<!-- BOM_TABLE_END -->"

# Directorio temporal relativo al directorio actual: cuando KICAD_CLI
# envuelve un "docker run" (como en action.yml), el contenedor solo tiene
# montado el repositorio como /work, así que todas las rutas que recibe
# kicad-cli, de entrada y de salida, deben ser relativas, nunca absolutas.
DIRECTORIO_TMP = "_retrata_tmp"

KICAD_CLI = shlex.split(os.environ.get("KICAD_CLI", "kicad-cli"))

EN_GITHUB = os.environ.get("GITHUB_ACTIONS") == "true"


def avisar(mensaje: str) -> None:
    if EN_GITHUB:
        print(f"::warning::{mensaje}")
    else:
        print(f"[aviso] {mensaje}", file=sys.stderr)


def fallar(mensaje: str) -> None:
    if EN_GITHUB:
        print(f"::error::{mensaje}")
    else:
        print(f"[error] {mensaje}", file=sys.stderr)
    sys.exit(1)


def correr_kicad(*argumentos: str) -> None:
    resultado = subprocess.run(
        [*KICAD_CLI, *argumentos], capture_output=True, text=True
    )
    if resultado.returncode != 0:
        fallar(
            f"kicad-cli {' '.join(argumentos)} falló:\n"
            f"{resultado.stdout}{resultado.stderr}"
        )


def leer_configuracion(ruta: Path) -> dict:
    if not ruta.is_file():
        fallar(f"no se encontró el archivo de configuración {ruta}")
    configuracion = yaml.safe_load(ruta.read_text()) or {}
    modulos = configuracion.get("modulos")
    if not modulos:
        fallar(f"{ruta} no define ningún módulo en 'modulos'")
    for modulo in modulos:
        for clave in ("nombre", "proyecto"):
            if not modulo.get(clave):
                fallar(f"un módulo en {ruta} no tiene '{clave}': {modulo}")
    return configuracion


# kicad-cli escribe la fecha de exportación en el <title> de cada SVG, así
# que dos exportaciones del mismo archivo nunca son idénticas byte a byte.
FECHA_SVG = re.compile(rb"<title>SVG Image created as .*? date [^<]*</title>")


def instalar_svg(nuevo: Path, destino: Path) -> None:
    # Si lo único que cambió es la fecha, se deja el SVG existente, para no
    # generar un commit en cada corrida.
    if destino.is_file():
        anterior = FECHA_SVG.sub(b"", destino.read_bytes())
        if anterior == FECHA_SVG.sub(b"", nuevo.read_bytes()):
            nuevo.unlink()
            print(f"sin cambios: {destino}")
            return
    shutil.move(str(nuevo), destino)
    print(f"actualizado: {destino}")


def generar_esquematico(nombre: str, kicad_sch: str, destino: Path) -> None:
    salida = Path(DIRECTORIO_TMP) / nombre
    salida.mkdir(parents=True, exist_ok=True)
    correr_kicad("sch", "export", "svg", "--output", str(salida), kicad_sch)
    # kicad-cli genera un SVG por hoja; la raíz se llama igual que el
    # .kicad_sch y las hojas jerárquicas "<raíz>-<hoja>.svg".
    svg = salida / f"{Path(kicad_sch).stem}.svg"
    if not svg.is_file():
        svgs = sorted(salida.glob("*.svg"))
        if not svgs:
            fallar(f"no se generó SVG del esquemático para {nombre}")
        svg = svgs[0]
    instalar_svg(svg, destino)


def generar_placa(nombre: str, kicad_pcb: str, capas: list[str], destino: Path) -> None:
    svg = Path(DIRECTORIO_TMP) / nombre / destino.name
    svg.parent.mkdir(parents=True, exist_ok=True)
    correr_kicad(
        "pcb", "export", "svg",
        "--layers", ",".join(capas),
        "--mode-single",
        "--output", str(svg),
        kicad_pcb,
    )
    instalar_svg(svg, destino)


def exportar_bom(kicad_sch: str) -> list[dict]:
    # kicad-cli no soporta escribir a stdout: "--output -" crea un archivo
    # literal llamado "-". Hay que darle un archivo real, con ruta relativa
    # (ver DIRECTORIO_TMP arriba).
    tmp = Path(DIRECTORIO_TMP) / f"bom_{Path(kicad_sch).stem}.csv"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    correr_kicad(
        "sch", "export", "bom",
        "--output", str(tmp),
        "--fields", "Reference,Value,Footprint,QUANTITY,${SYMBOL_LIBRARY},${SYMBOL_NAME}",
        "--labels", "Refs,Value,Footprint,Qty,Lib,Name",
        "--group-by", "Value,Footprint",
        "--ref-range-delimiter", "",
        "--ref-delimiter", ", ",
        kicad_sch,
    )
    with open(tmp, newline="") as f:
        return list(csv.DictReader(f))


def generar_tabla(filas: list[dict], descripciones: dict) -> str:
    lineas = [
        "| Referencias | Cantidad | Valor | Huella | Descripción |",
        "| --- | --- | --- | --- | --- |",
    ]
    total = 0
    for fila in filas:
        lib_id = f"{fila['Lib']}:{fila['Name']}"
        descripcion = descripciones.get(lib_id)
        if descripcion is None:
            avisar(f"sin descripción para '{lib_id}', agregarla en 'descripciones' del archivo de configuración")
            descripcion = lib_id
        valor = fila["Value"] or "*(valor sin definir)*"
        huella = fila["Footprint"] or "*(sin huella asignada)*"
        total += int(fila["Qty"])
        lineas.append(f"| {fila['Refs']} | {fila['Qty']} | {valor} | {huella} | {descripcion} |")
    lineas.append("")
    faltan_valor = any(f["Value"] == "" for f in filas)
    faltan_huella = any(f["Footprint"] == "" for f in filas)
    if faltan_valor or faltan_huella:
        lineas.append(
            f"{total} componentes en total. Los ítems marcados "
            + ("*(valor sin definir)*" if faltan_valor else "")
            + (" y " if faltan_valor and faltan_huella else "")
            + ("*(sin huella asignada)*" if faltan_huella else "")
            + " todavía no están completos en el esquemático — hay que completarlos antes de generar gerbers o comprar partes para esta revisión."
        )
    else:
        lineas.append(f"{total} componentes en total.")
    return "\n".join(lineas)


def actualizar_doc(doc: Path, tabla: str) -> None:
    if not doc.is_file():
        fallar(f"no se encontró el documento {doc}")
    texto = doc.read_text()
    patron = re.compile(
        re.escape(INICIO_MARCA) + r".*?" + re.escape(FIN_MARCA), re.S
    )
    # Líneas en blanco alrededor de la tabla: sin ellas kramdown (Jekyll) la
    # funde con el comentario HTML en un párrafo y no la renderiza como tabla.
    reemplazo = f"{INICIO_MARCA}\n\n{tabla}\n\n{FIN_MARCA}"
    nuevo_texto, n = patron.subn(lambda _: reemplazo, texto)
    if n == 0:
        fallar(f"no se encontraron las marcas {INICIO_MARCA}/{FIN_MARCA} en {doc}")
    if nuevo_texto != texto:
        doc.write_text(nuevo_texto)
        print(f"actualizado: {doc}")
    else:
        print(f"sin cambios: {doc}")


def retratar_modulo(modulo: dict, configuracion: dict, descripciones: dict) -> list[Path]:
    nombre = modulo["nombre"]
    proyecto = modulo["proyecto"]
    # "proyecto" puede venir con o sin extensión (.kicad_pro, .kicad_sch...).
    base = re.sub(r"\.kicad_(pro|sch|pcb)$", "", proyecto)
    kicad_sch = f"{base}.kicad_sch"
    kicad_pcb = f"{base}.kicad_pcb"
    carpeta_imagenes = Path(configuracion.get("carpeta-imagenes", "docs/images"))
    carpeta_imagenes.mkdir(parents=True, exist_ok=True)
    rutas = []

    print(f"== {nombre} ({base})")

    if modulo.get("esquematico", True):
        if not Path(kicad_sch).is_file():
            fallar(f"no existe {kicad_sch} (módulo {nombre})")
        destino = carpeta_imagenes / f"{nombre}-esquematico.svg"
        generar_esquematico(nombre, kicad_sch, destino)
        rutas.append(destino)

    if modulo.get("placa", True):
        if not Path(kicad_pcb).is_file():
            fallar(f"no existe {kicad_pcb} (módulo {nombre})")
        capas = modulo.get("capas-placa") or configuracion.get("capas-placa") or CAPAS_PLACA_BASE
        destino = carpeta_imagenes / f"{nombre}-placa.svg"
        generar_placa(nombre, kicad_pcb, capas, destino)
        rutas.append(destino)

    if modulo.get("doc"):
        if not Path(kicad_sch).is_file():
            fallar(f"no existe {kicad_sch} (módulo {nombre})")
        doc = Path(modulo["doc"])
        tabla = generar_tabla(exportar_bom(kicad_sch), descripciones)
        actualizar_doc(doc, tabla)
        rutas.append(doc)

    return rutas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--configuracion", default="kicad-retrata.yml",
                        help="archivo de configuración (por defecto: kicad-retrata.yml)")
    parser.add_argument("--rutas", default=None,
                        help="si se da, escribe acá la lista de archivos generados, uno por línea")
    argumentos = parser.parse_args()

    configuracion = leer_configuracion(Path(argumentos.configuracion))
    descripciones = {**DESCRIPCIONES_BASE, **(configuracion.get("descripciones") or {})}

    shutil.rmtree(DIRECTORIO_TMP, ignore_errors=True)
    rutas = []
    try:
        for modulo in configuracion["modulos"]:
            rutas += retratar_modulo(modulo, configuracion, descripciones)
    finally:
        shutil.rmtree(DIRECTORIO_TMP, ignore_errors=True)

    if argumentos.rutas:
        Path(argumentos.rutas).write_text("".join(f"{ruta}\n" for ruta in rutas))


if __name__ == "__main__":
    main()
