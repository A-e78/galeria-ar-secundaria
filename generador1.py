import os

# 1. Plantilla inicial de la página web
html = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Galería AR - Escuela</title>
    <!-- Importar el componente model-viewer de Google -->
    <script type="module" src="https://ajax.googleapis.com/ajax/libs/model-viewer/4.0.0/model-viewer.min.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; text-align: center; background-color: #f4f4f9; padding: 20px; margin: 0; }
        .contenedor { max-width: 600px; margin: 0 auto; background: white; padding: 20px; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
        h1 { color: #333; font-size: 24px; margin-bottom: 10px; }
        p { color: #666; font-size: 15px; margin-bottom: 20px; }
        
        /* Estilos para el menú desplegable */
        select { 
            font-size: 16px; 
            padding: 12px; 
            width: 100%; 
            margin-bottom: 20px; 
            border-radius: 8px; 
            border: 2px solid #ddd; 
            background-color: #fff;
            cursor: pointer;
            outline: none;
            transition: border-color 0.3s;
        }
        select:focus { border-color: #007bff; }
        
        /* Estilos para el visor 3D */
        model-viewer { 
            width: 100%; 
            height: 400px; 
            background-color: #eef2f5; 
            border-radius: 8px;
            display: none; 
        }
        
        /* Instrucción para el usuario */
        #mensaje-instruccion {
            padding: 40px 20px;
            color: #888;
            border: 2px dashed #ddd;
            border-radius: 8px;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="contenedor">
        <h1>Exposición AR Escolar</h1>
        <p>Selecciona el diseño de un alumno en el menú y luego usa el botón en la esquina inferior derecha del visor para proyectarlo en el patio.</p>
        
        <!-- El menú desplegable -->
        <select id="selector-modelo" onchange="cambiarModelo()">
            <option value="">-- Selecciona un diseño --</option>
"""

# 2. Leer los modelos y generar las opciones (options) para el menú
if not os.path.exists("modelos"):
    os.makedirs("modelos")
    print("Se creó la carpeta 'modelos'. Por favor, coloca allí tus archivos.")

# Solo buscamos los .glb para armar la lista del menú, el JS se encargará del .usdz
archivos = [f for f in os.listdir("modelos") if f.endswith(".glb")]

for archivo in archivos:
    nombre_limpio = archivo.replace(".glb", "").replace("_", " ").title()
    html += f'            <option value="modelos/{archivo}">{nombre_limpio}</option>\n'

# 3. Completar el HTML con el visor y la lógica JavaScript adaptada para iOS y Android
html += """        </select>

        <div id="mensaje-instruccion">
            Selecciona un modelo arriba para comenzar a visualizarlo.
        </div>

        <!-- El visor AR adaptado -->
        <!-- exposure="1.2" ayuda a compensar la palidez en Android -->
        <model-viewer id="visor-ar" 
                      src="" 
                      ios-src="" 
                      ar 
                      ar-modes="scene-viewer quick-look" 
                      ar-scale="auto"
                      camera-controls 
                      auto-rotate 
                      shadow-intensity="1"
                      exposure="1.2">
        </model-viewer>
    </div>

    <!-- Script para cambiar el modelo dinámicamente y asignar el .usdz para iOS -->
    <script>
        function cambiarModelo() {
            const selector = document.getElementById('selector-modelo');
            const visor = document.getElementById('visor-ar');
            const instruccion = document.getElementById('mensaje-instruccion');
            const rutaModeloGLB = selector.value;

            if (rutaModeloGLB) {
                // Asignar el GLB para Web y Android
                visor.src = rutaModeloGLB;
                
                // Generar dinámicamente la ruta del USDZ para iOS
                const rutaModeloUSDZ = rutaModeloGLB.replace(".glb", ".usdz");
                visor.setAttribute("ios-src", rutaModeloUSDZ);
                
                visor.style.display = "block";
                instruccion.style.display = "none";
            } else {
                visor.src = "";
                visor.removeAttribute("ios-src");
                visor.style.display = "none";
                instruccion.style.display = "block";
            }
        }
    </script>
</body>
</html>
"""

# Guardar el archivo HTML
with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"¡Archivo index.html generado con éxito! Menú listo para cargar GLB y USDZ de {len(archivos)} modelos.")
