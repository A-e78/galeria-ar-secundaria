import os
import json
import struct
import shutil
from html import escape

CARPETA_MODELOS = "modelos"
CARPETA_AR = "modelos_ar"


def corregir_glb(origen, destino):
    """
    Crea una copia del GLB con materiales no metálicos.
    Conserva la geometría, los colores base y las texturas.
    Requiere un archivo GLB versión 2.
    """
    with open(origen, "rb") as archivo:
        datos = archivo.read()

    if len(datos) < 12:
        raise ValueError("Archivo demasiado pequeño para ser GLB")

    magic, version, _ = struct.unpack_from("<4sII", datos, 0)

    if magic != b"glTF" or version != 2:
        raise ValueError("El archivo no es GLB versión 2")

    offset = 12
    bloques = []
    materiales_modificados = 0
    encontro_json = False

    while offset < len(datos):
        if offset + 8 > len(datos):
            raise ValueError("Cabecera de bloque incompleta")

        longitud, tipo = struct.unpack_from(
            "<I4s", datos, offset
        )
        offset += 8

        contenido = datos[offset:offset + longitud]

        if len(contenido) != longitud:
            raise ValueError("Bloque GLB incompleto")

        offset += longitud

        if tipo == b"JSON":
            documento = json.loads(
                contenido.decode("utf-8").rstrip("\x00 \t\r\n")
            )
            encontro_json = True

            for material in documento.get("materials", []):
                pbr = material.setdefault(
                    "pbrMetallicRoughness", {}
                )

                # Adecuado para materiales de apariencia plástica.
                pbr["metallicFactor"] = 0.0
                pbr["roughnessFactor"] = 0.85

                materiales_modificados += 1

            contenido = json.dumps(
                documento,
                ensure_ascii=False,
                separators=(",", ":")
            ).encode("utf-8")

            # El bloque JSON del GLB debe alinearse a 4 bytes.
            contenido += b" " * ((-len(contenido)) % 4)

        bloques.append((tipo, contenido))

    if not encontro_json:
        raise ValueError("No se encontró el bloque JSON del GLB")

    longitud_total = 12 + sum(
        8 + len(contenido) for _, contenido in bloques
    )

    with open(destino, "wb") as archivo:
        archivo.write(
            struct.pack("<4sII", b"glTF", 2, longitud_total)
        )

        for tipo, contenido in bloques:
            archivo.write(
                struct.pack("<I4s", len(contenido), tipo)
            )
            archivo.write(contenido)

    return materiales_modificados


# 1. Crear carpetas si no existen.
os.makedirs(CARPETA_MODELOS, exist_ok=True)
os.makedirs(CARPETA_AR, exist_ok=True)

# 2. Buscar archivos GLB, incluso si su extensión está en mayúsculas.
archivos = sorted(
    f for f in os.listdir(CARPETA_MODELOS)
    if f.lower().endswith(".glb")
)

# 3. Generar copias ajustadas, sin sobrescribir los originales.
for archivo in archivos:
    origen = os.path.join(CARPETA_MODELOS, archivo)
    destino = os.path.join(CARPETA_AR, archivo)

    try:
        cantidad = corregir_glb(origen, destino)
        print(f"OK: {archivo} — {cantidad} materiales revisados")

    except (ValueError, struct.error, json.JSONDecodeError) as error:
        # Si el archivo no se puede ajustar, se conserva una copia
        # original para no excluirlo de la galería.
        shutil.copy2(origen, destino)
        print(f"AVISO: {archivo} se copió sin ajustar: {error}")


# 4. Crear las opciones del menú.
opciones = "\n".join(
    f'<option value="{escape(CARPETA_AR + "/" + archivo, quote=True)}">'
    f'{escape(os.path.splitext(archivo)[0].replace("_", " ").title())}'
    f'</option>'
    for archivo in archivos
)


# 5. Generar la página web.
html = """<!DOCTYPE html>
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

html = html.replace("<!-- OPCIONES_MODELOS -->", opciones)

with open("index.html", "w", encoding="utf-8") as archivo:
    archivo.write(html)

print(f"Galería generada: {len(archivos)} modelos encontrados.")
print("Publica index.html junto con la carpeta modelos_ar.")
