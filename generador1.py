"""
Genera la galería AR escolar (index.html) a partir de los modelos GLB.

Uso:
    1. Guarda los GLB de los alumnos en la carpeta "modelos".
    2. Ejecuta:  python generar_galeria.py
    3. Publica index.html junto con la carpeta "modelos_ar".

Por qué se procesan los GLB:
    En iPhone, el botón de AR abre Quick Look, que no lee GLB: model-viewer
    convierte el modelo a USDZ al vuelo. Quick Look no soporta los colores
    de vértice (COLOR_0), así que las piezas coloreadas de esa manera salen
    blancas. Este script pasa esos colores al material de cada pieza, que
    es lo único que Quick Look sí entiende, y además imprime un informe de
    los colores que trae cada modelo para detectar otros problemas.
"""

import copy
import json
import os
import shutil
import struct
from html import escape

CARPETA_MODELOS = "modelos"
CARPETA_AR = "modelos_ar"

# Si una pieza mezcla más colores que este número (por ejemplo, un
# degradado), no se separa por colores: se usa su color promedio.
MAX_COLORES_POR_PIEZA = 64

BLANCO = (1.0, 1.0, 1.0)

# Tipos de datos de glTF: código -> (formato de struct, bytes por valor).
TIPOS_COMPONENTE = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}

VALORES_POR_TIPO = {
    "SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
    "MAT2": 4, "MAT3": 9, "MAT4": 16,
}

# Extensiones que pueden dar problemas al convertir a USDZ para iPhone.
EXTENSIONES_RIESGOSAS = {
    "KHR_draco_mesh_compression": "geometría comprimida con Draco",
    "EXT_meshopt_compression": "geometría comprimida con meshopt",
    "KHR_texture_basisu": "texturas KTX2",
    "KHR_materials_unlit": "materiales sin iluminación (unlit)",
}


# ---------------------------------------------------------------------------
# Lectura y escritura de archivos GLB
# ---------------------------------------------------------------------------

def leer_glb(ruta):
    """Devuelve (documento JSON, bloque binario o None, lista de bloques)."""
    with open(ruta, "rb") as archivo:
        datos = archivo.read()

    if len(datos) < 12:
        raise ValueError("Archivo demasiado pequeño para ser GLB")

    magic, version, _ = struct.unpack_from("<4sII", datos, 0)

    if magic != b"glTF" or version != 2:
        raise ValueError("El archivo no es GLB versión 2")

    offset = 12
    bloques = []

    while offset < len(datos):
        if offset + 8 > len(datos):
            raise ValueError("Cabecera de bloque incompleta")

        longitud, tipo = struct.unpack_from("<I4s", datos, offset)
        offset += 8

        contenido = datos[offset:offset + longitud]

        if len(contenido) != longitud:
            raise ValueError("Bloque GLB incompleto")

        offset += longitud
        bloques.append((tipo, contenido))

    if not bloques or bloques[0][0] != b"JSON":
        raise ValueError("No se encontró el bloque JSON del GLB")

    documento = json.loads(
        bloques[0][1].decode("utf-8").rstrip("\x00 \t\r\n")
    )

    binario = None
    for tipo, contenido in bloques[1:]:
        if tipo == b"BIN\x00":
            binario = bytearray(contenido)
            break

    return documento, binario, bloques


def escribir_glb(ruta, documento, binario, bloques):
    """Guarda un GLB con el JSON y el bloque binario ya modificados."""
    texto = json.dumps(
        documento, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")

    # Los bloques del GLB deben alinearse a 4 bytes.
    texto += b" " * ((-len(texto)) % 4)

    salida = []
    binario_escrito = False

    for tipo, contenido in bloques:
        if tipo == b"JSON":
            contenido = texto
        elif tipo == b"BIN\x00" and not binario_escrito:
            contenido = bytes(binario) + b"\x00" * ((-len(binario)) % 4)
            binario_escrito = True

        salida.append((tipo, contenido))

    longitud_total = 12 + sum(8 + len(contenido) for _, contenido in salida)

    with open(ruta, "wb") as archivo:
        archivo.write(struct.pack("<4sII", b"glTF", 2, longitud_total))

        for tipo, contenido in salida:
            archivo.write(struct.pack("<I4s", len(contenido), tipo))
            archivo.write(contenido)


def leer_accesor(documento, binario, indice):
    """Lee los datos de un accesor glTF y los devuelve como lista de tuplas."""
    accesor = documento["accessors"][indice]

    if "sparse" in accesor or "bufferView" not in accesor:
        raise ValueError("los datos del modelo no se pueden leer")

    vista = documento["bufferViews"][accesor["bufferView"]]

    if binario is None or vista.get("buffer", 0) != 0:
        raise ValueError("los datos no están en el bloque binario del GLB")

    formato, tamano = TIPOS_COMPONENTE[accesor["componentType"]]
    valores = VALORES_POR_TIPO[accesor["type"]]
    paso = vista.get("byteStride") or tamano * valores
    inicio = vista.get("byteOffset", 0) + accesor.get("byteOffset", 0)
    lector = struct.Struct("<" + formato * valores)

    return [
        lector.unpack_from(binario, inicio + i * paso)
        for i in range(accesor["count"])
    ]


def agregar_indices(documento, binario, indices):
    """Añade una lista de índices al bloque binario y devuelve su accesor."""
    while len(binario) % 4:
        binario.append(0)

    documento["bufferViews"].append({
        "buffer": 0,
        "byteOffset": len(binario),
        "byteLength": len(indices) * 4,
        "target": 34963,
    })
    binario.extend(struct.pack("<%dI" % len(indices), *indices))

    documento["accessors"].append({
        "bufferView": len(documento["bufferViews"]) - 1,
        "componentType": 5125,
        "count": len(indices),
        "type": "SCALAR",
    })

    return len(documento["accessors"]) - 1


# ---------------------------------------------------------------------------
# Correcciones para iPhone (Quick Look)
# ---------------------------------------------------------------------------

def promedio(colores):
    n = len(colores)
    return tuple(round(sum(c[k] for c in colores) / n, 4) for k in range(3))


def agrupar_por_color(documento, binario, pieza):
    """
    Agrupa los triángulos de una pieza según su color de vértice.

    Devuelve (grupos, degradado):
      - grupos: {color: lista de índices}. Si toda la pieza tiene un solo
        color, el valor es None (no hace falta tocar los índices).
      - degradado: True si había demasiados colores y se usó el promedio.
    """
    indice_color = pieza["attributes"]["COLOR_0"]
    tipo = documento["accessors"][indice_color]["componentType"]
    divisor = {5121: 255.0, 5123: 65535.0}.get(tipo, 1.0)

    colores = [
        tuple(valor / divisor for valor in color[:3])
        for color in leer_accesor(documento, binario, indice_color)
    ]

    if not colores:
        raise ValueError("COLOR_0 está vacío")

    unicos = {tuple(round(v, 4) for v in color) for color in colores}

    if len(unicos) == 1:
        return {unicos.pop(): None}, False

    # Líneas o puntos: no hay triángulos que separar.
    if pieza.get("mode", 4) != 4:
        return {promedio(colores): None}, True

    if "indices" in pieza:
        indices = [v[0] for v in leer_accesor(documento, binario, pieza["indices"])]
    else:
        indices = range(len(colores))

    grupos = {}

    for i in range(0, len(indices) - 2, 3):
        a, b, c = indices[i], indices[i + 1], indices[i + 2]
        color = tuple(
            round((colores[a][k] + colores[b][k] + colores[c][k]) / 3.0, 4)
            for k in range(3)
        )
        grupos.setdefault(color, []).extend((a, b, c))

        if len(grupos) > MAX_COLORES_POR_PIEZA:
            return {promedio(colores): None}, True

    if not grupos:
        return {promedio(colores): None}, False

    return grupos, False


def convertir_colores_de_vertice(documento, binario):
    """
    Pasa los colores de vértice (COLOR_0) al material de cada pieza.

    Quick Look ignora COLOR_0, así que sin esto las piezas salen blancas.
    Si una misma pieza mezcla varios colores, se separa en una pieza por
    color (comparten los mismos vértices; solo cambian los índices).

    Devuelve (cantidad de colores convertidos, lista de avisos).
    """
    nuevos_materiales = {}
    convertidos = 0
    avisos = []

    def material_para(indice_base, color):
        if color == BLANCO:
            return indice_base  # multiplicar por blanco no cambia nada

        clave = (indice_base, color)

        if clave not in nuevos_materiales:
            materiales = documento.setdefault("materials", [])
            base = materiales[indice_base] if indice_base is not None else {}
            material = copy.deepcopy(base)
            pbr = material.setdefault("pbrMetallicRoughness", {})
            factor = pbr.get("baseColorFactor", [1.0, 1.0, 1.0, 1.0])

            # En glTF el color final es: color de vértice x color del material.
            pbr["baseColorFactor"] = [
                factor[0] * color[0],
                factor[1] * color[1],
                factor[2] * color[2],
                factor[3],
            ]

            materiales.append(material)
            nuevos_materiales[clave] = len(materiales) - 1

        return nuevos_materiales[clave]

    for malla in documento.get("meshes", []):
        piezas = []

        for pieza in malla.get("primitives", []):
            if "COLOR_0" not in pieza.get("attributes", {}):
                piezas.append(pieza)
                continue

            try:
                grupos, degradado = agrupar_por_color(documento, binario, pieza)
            except (ValueError, KeyError, IndexError, struct.error) as error:
                avisos.append(f"no se pudieron leer los colores de vértice ({error})")
                piezas.append(pieza)
                continue

            if degradado:
                avisos.append(
                    "una pieza tiene demasiados colores distintos "
                    "(¿degradado?): se usó su color promedio"
                )

            base = pieza.get("material")
            convertidos += sum(1 for color in grupos if color != BLANCO)

            if len(grupos) == 1:
                (color, _), = grupos.items()
                del pieza["attributes"]["COLOR_0"]

                nuevo = material_para(base, color)
                if nuevo is not None:
                    pieza["material"] = nuevo

                piezas.append(pieza)
                continue

            for color, indices in grupos.items():
                parte = copy.deepcopy(pieza)
                del parte["attributes"]["COLOR_0"]
                parte["indices"] = agregar_indices(documento, binario, indices)

                nuevo = material_para(base, color)
                if nuevo is not None:
                    parte["material"] = nuevo

                piezas.append(parte)

        malla["primitives"] = piezas

    return convertidos, avisos


def convertir_specular_glossiness(documento):
    """
    Pasa los materiales antiguos (specular-glossiness) a metallic-roughness,
    conservando su color. Con el formato antiguo los colores se pierden.
    """
    extension = "KHR_materials_pbrSpecularGlossiness"
    convertidos = 0

    for material in documento.get("materials", []):
        datos = material.get("extensions", {}).pop(extension, None)

        if datos is None:
            continue

        pbr = material.setdefault("pbrMetallicRoughness", {})

        if "diffuseFactor" in datos:
            pbr["baseColorFactor"] = datos["diffuseFactor"]

        if "diffuseTexture" in datos:
            pbr["baseColorTexture"] = datos["diffuseTexture"]

        if not material["extensions"]:
            del material["extensions"]

        convertidos += 1

    for clave in ("extensionsUsed", "extensionsRequired"):
        if extension in documento.get(clave, []):
            documento[clave].remove(extension)

            if not documento[clave]:
                del documento[clave]

    return convertidos


def ajustar_materiales(documento):
    """Materiales no metálicos, de apariencia plástica."""
    materiales = documento.get("materials", [])

    for material in materiales:
        pbr = material.setdefault("pbrMetallicRoughness", {})
        pbr["metallicFactor"] = 0.0
        pbr["roughnessFactor"] = 0.85

    return len(materiales)


# ---------------------------------------------------------------------------
# Informe de cada modelo
# ---------------------------------------------------------------------------

def color_a_hex(rgb):
    """Color lineal de glTF -> hexadecimal sRGB (solo para el informe)."""
    def canal(c):
        c = max(0.0, min(1.0, c))
        c = 12.92 * c if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        return round(c * 255)

    return "#%02x%02x%02x" % tuple(canal(c) for c in rgb[:3])


def resumir_materiales(documento):
    """Devuelve (colores en hexadecimal, cantidad de materiales con textura)."""
    colores = []
    texturas = 0

    usados = {
        pieza["material"]
        for malla in documento.get("meshes", [])
        for pieza in malla.get("primitives", [])
        if "material" in pieza
    }

    for indice in sorted(usados):
        pbr = documento["materials"][indice].get("pbrMetallicRoughness", {})

        if "baseColorTexture" in pbr:
            texturas += 1

        hexadecimal = color_a_hex(pbr.get("baseColorFactor", [1, 1, 1, 1]))

        if hexadecimal != "#ffffff" and hexadecimal not in colores:
            colores.append(hexadecimal)

    return colores, texturas


def corregir_glb(origen, destino):
    """
    Crea una copia del GLB preparada para verse bien en iPhone y Android.
    Conserva la geometría, los colores base y las texturas.
    Requiere un archivo GLB versión 2.
    """
    documento, binario, bloques = leer_glb(origen)
    avisos = []

    for extension in documento.get("extensionsUsed", []):
        if extension in EXTENSIONES_RIESGOSAS:
            avisos.append(
                f"usa {extension} ({EXTENSIONES_RIESGOSAS[extension]}), "
                "que puede no verse bien en iPhone"
            )

    # 1. Materiales antiguos -> metallic-roughness.
    convertir_specular_glossiness(documento)

    # 2. Colores de vértice -> color de material (lo que entiende Quick Look).
    tamano_inicial = len(binario) if binario is not None else 0
    convertidos, avisos_color = convertir_colores_de_vertice(documento, binario)
    avisos.extend(avisos_color)

    if binario is not None and len(binario) != tamano_inicial:
        while len(binario) % 4:
            binario.append(0)
        documento["buffers"][0]["byteLength"] = len(binario)

    # 3. Materiales no metálicos.
    ajustar_materiales(documento)

    colores, texturas = resumir_materiales(documento)
    sin_color = not colores and texturas == 0

    escribir_glb(destino, documento, binario, bloques)

    return {
        "colores_de_vertice": convertidos,
        "colores": colores,
        "texturas": texturas,
        "sin_color": sin_color,
        "avisos": avisos,
    }


# ---------------------------------------------------------------------------
# Página web
# ---------------------------------------------------------------------------

PLANTILLA_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#f4f4f9">
    <title>Galería AR Escolar</title>

    <script type="module"
        src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js">
    </script>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            font-family: Arial, sans-serif;
            text-align: center;
            background: #f4f4f9;
            padding: 20px;
            margin: 0;
        }

        .contenedor {
            max-width: 600px;
            margin: 0 auto;
            background: white;
            padding: 20px;
            border-radius: 14px;
            box-shadow: 0 4px 12px rgba(0,0,0,.1);
        }

        h1 {
            color: #333;
            font-size: 27px;
        }

        p {
            color: #666;
            line-height: 1.5;
        }

        select {
            width: 100%;
            padding: 14px;
            margin: 12px 0 20px;
            border: 2px solid #ddd;
            border-radius: 9px;
            background: white;
            font-size: 16px;
        }

        model-viewer {
            width: 100%;
            height: 430px;
            display: none;
            background-color: #eef2f5;
            border-radius: 10px;

            /* Solo afecta al renderizado del visor web. */
            exposure: 1;
        }

        #mensaje-instruccion {
            padding: 40px 20px;
            color: #888;
            border: 2px dashed #ddd;
            border-radius: 8px;
        }
    </style>
</head>

<body>
    <main class="contenedor">
        <h1>Exposición AR Escolar</h1>

        <p>
            Selecciona el diseño de un alumno y utiliza el botón
            de realidad aumentada del visor para colocarlo en tu entorno.
        </p>

        <select id="selector-modelo">
            <option value="">-- Selecciona un diseño --</option>
            <!-- OPCIONES_MODELOS -->
        </select>

        <div id="mensaje-instruccion">
            Selecciona un modelo para comenzar a visualizarlo.
        </div>

        <model-viewer
            id="visor-ar"
            ar
            ar-modes="webxr scene-viewer quick-look"
            ar-scale="auto"
            ar-placement="floor"
            camera-controls
            auto-rotate
            shadow-intensity="0.8"
            exposure="1">
        </model-viewer>
    </main>

    <script>
        const selector = document.getElementById("selector-modelo");
        const visor = document.getElementById("visor-ar");
        const instruccion = document.getElementById("mensaje-instruccion");

        /*
         * Registrar el evento antes de empezar a cargar los modelos.
         * Las correcciones se aplican al visor web y a WebXR.
         */
        visor.addEventListener("load", () => {
            if (!visor.model) return;

            for (const material of visor.model.materials) {
                const pbr = material.pbrMetallicRoughness;

                if (!pbr) continue;

                pbr.setMetallicFactor(0);
                pbr.setRoughnessFactor(0.85);
            }

            console.log("Modelo cargado y materiales ajustados.");
        });

        function cambiarModelo() {
            const ruta = selector.value;

            if (!ruta) {
                visor.removeAttribute("src");
                visor.removeAttribute("ios-src");
                visor.style.display = "none";
                instruccion.style.display = "block";
                return;
            }

            /*
             * Se utiliza el GLB corregido como fuente.
             * No asignar ios-src: model-viewer generará el USDZ
             * automáticamente cuando se active Quick Look.
             */
            visor.removeAttribute("ios-src");

            instruccion.style.display = "none";
            visor.style.display = "block";
            visor.src = ruta;
        }

        selector.addEventListener("change", cambiarModelo);
    </script>
</body>
</html>
"""


def main():
    # 1. Crear carpetas si no existen.
    os.makedirs(CARPETA_MODELOS, exist_ok=True)
    os.makedirs(CARPETA_AR, exist_ok=True)

    # 2. Buscar archivos GLB, incluso si su extensión está en mayúsculas.
    archivos = sorted(
        f for f in os.listdir(CARPETA_MODELOS)
        if f.lower().endswith(".glb")
    )

    # 3. Generar copias ajustadas, sin sobrescribir los originales.
    sin_color = []

    for archivo in archivos:
        origen = os.path.join(CARPETA_MODELOS, archivo)
        destino = os.path.join(CARPETA_AR, archivo)

        try:
            informe = corregir_glb(origen, destino)

        except Exception as error:
            # Si el archivo no se puede ajustar, se conserva una copia
            # original para no excluirlo de la galería.
            shutil.copy2(origen, destino)
            print(f"AVISO: {archivo} se copió sin ajustar: {error}")
            continue

        print(f"OK: {archivo}")

        if informe["colores_de_vertice"]:
            print(
                "    · colores de vértice pasados al material: "
                f"{informe['colores_de_vertice']}"
            )

        if informe["colores"]:
            print("    · colores del modelo: " + " ".join(informe["colores"]))

        if informe["texturas"]:
            print(f"    · materiales con textura: {informe['texturas']}")

        for aviso in informe["avisos"]:
            print(f"    · AVISO: {aviso}")

        if informe["sin_color"]:
            sin_color.append(archivo)

    if sin_color:
        print()
        print("ATENCIÓN: estos modelos no traen color en el archivo,")
        print("así que se verán blancos en cualquier dispositivo:")

        for archivo in sin_color:
            print(f"  - {archivo}")

        print("Hay que exportarlos de nuevo desde el programa de diseño,")
        print("comprobando que el color se guarde en el GLB.")

    # 4. Crear las opciones del menú.
    opciones = "\n".join(
        f'<option value="{escape(CARPETA_AR + "/" + archivo, quote=True)}">'
        f'{escape(os.path.splitext(archivo)[0].replace("_", " ").title())}'
        f'</option>'
        for archivo in archivos
    )

    # 5. Generar la página web.
    html = PLANTILLA_HTML.replace("<!-- OPCIONES_MODELOS -->", opciones)

    with open("index.html", "w", encoding="utf-8") as archivo:
        archivo.write(html)

    print()
    print(f"Galería generada: {len(archivos)} modelos encontrados.")
    print("Publica index.html junto con la carpeta modelos_ar.")


if __name__ == "__main__":
    main()
