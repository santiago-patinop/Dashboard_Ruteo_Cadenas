"""
Construye ruteo_colab.ipynb: notebook autocontenido para Google Colab.

El notebook lleva empaquetados (zip en base64) los mismos archivos del repositorio:
ruteo.py, vista_colab.py, el tablero (static/) y el Excel base. Así hay una sola
implementación del algoritmo. Volver a ejecutar este script después de cambiar el código.

Uso:  python scripts/construir_notebook_colab.py
"""

import base64
import io
import zipfile
from pathlib import Path

import nbformat as nbf

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVOS = ["ruteo.py", "vista_colab.py", "static/index.html", "static/app.css", "static/app.js",
            "datos/Reto_VRP_Medellin_Datos.xlsx"]


def paquete_base64() -> str:
    """Zip en memoria con los archivos del proyecto, codificado en base64."""
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in ARCHIVOS:
            z.write(RAIZ / rel, rel)
    return base64.b64encode(salida.getvalue()).decode()


def construir():
    nb = nbf.v4.new_notebook()
    celdas = []
    md = lambda t: celdas.append(nbf.v4.new_markdown_cell(t.strip()))
    code = lambda t: celdas.append(nbf.v4.new_code_cell(t.strip()))

    md("""
# Plan de rutas de distribución — Valle de Aburrá

Este notebook calcula, para cada día del archivo de Excel, las rutas de **mínimo costo** para la flota que sale del
**CD La Macarena** y regresa al mismo CD. Respeta la capacidad de cada vehículo, la jornada de 8 horas y los vehículos disponibles.

### Cómo usarlo

1. En el menú, elija **Entorno de ejecución → Ejecutar todas** (o `Ctrl + F9`).
2. En el paso 3 aparece el botón **Elegir archivos**. Seleccione su Excel (`.xlsx`).
   - Puede ser el libro completo, con las hojas Clientes, Matriz_Distancias, Demanda y Vehiculos.
   - También puede ser un libro con **solo la hoja Demanda**. En ese caso se usan los clientes, la matriz y la flota del archivo base.
3. Espere el cálculo, que toma unos 30 segundos por día. Colab resuelve 2 días a la vez, así que una semana de 6 días tarda cerca de 1,5 minutos. Los resultados aparecen abajo.
4. En el paso 7 se descargan **rutas.xlsx** y el **tablero interactivo** (un archivo HTML que se abre en cualquier navegador).

Para resolver otros días, por ejemplo Viernes y Sábado, llene esas columnas en la hoja Demanda y cargue el archivo.
**No hay que modificar código.** Un día con la columna vacía se informa como "Sin datos".

> La primera ejecución instala OR-Tools, lo que toma unos 30 a 60 segundos.
""")

    code(f'''
#@title 1. Preparar el entorno (instala OR-Tools y el código del modelo) {{ display-mode: "form" }}
import base64, importlib, io, os, subprocess, sys, urllib.request, zipfile
from pathlib import Path

def _instalar(paquete, modulo):
    """Instala un paquete solo si no está disponible."""
    try:
        importlib.import_module(modulo)
    except ImportError:
        print(f"Instalando {{paquete}}…")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", paquete], check=True)

for _paquete, _modulo in [("ortools>=9.10", "ortools"), ("folium", "folium"), ("openpyxl", "openpyxl")]:
    _instalar(_paquete, _modulo)
try:
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    from ortools.sat.python import cp_model
except Exception:
    # La instalación actualizó una librería ya cargada (p. ej. protobuf): hay que reiniciar la sesión.
    print("⚠ Se actualizaron librerías del sistema. Colab reiniciará la sesión en unos segundos.\\n"
          "  Cuando termine, vuelva a elegir: Entorno de ejecución → Ejecutar todas.")
    os.kill(os.getpid(), 9)

# Código del modelo y del tablero (los mismos archivos del proyecto, empaquetados).
PAQUETE = "{paquete_base64()}"
zipfile.ZipFile(io.BytesIO(base64.b64decode(PAQUETE))).extractall(".")

# Librerías del tablero exportable (si no hay descarga, el HTML las enlaza desde internet).
os.makedirs("static/vendor", exist_ok=True)
for _destino, _url in {{
    "static/vendor/leaflet.css": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
    "static/vendor/leaflet.js": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
    "static/vendor/chart.umd.min.js": "https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.js",
}}.items():
    if not Path(_destino).exists():
        try:
            urllib.request.urlretrieve(_url, _destino)
        except Exception:
            pass

sys.path.insert(0, os.getcwd())
import ruteo, vista_colab
importlib.reload(ruteo); importlib.reload(vista_colab)
from IPython.display import HTML, display
import pandas as pd

try:
    from google.colab import files as _colab_files
    EN_COLAB = True
except ImportError:
    EN_COLAB = False
print("✓ Entorno listo" + (" (Google Colab)" if EN_COLAB else " (Jupyter local)"))
''')

    md("""
## 2. Parámetros de operación

Los valores por defecto son los del caso. Puede cambiarlos en el formulario de la derecha y volver a ejecutar desde aquí.
""")
    code('''
#@title 2. Parámetros { display-mode: "form" }
VELOCIDAD_KMH = 24  #@param {type:"number"}
DESCARGA_MIN_POR_CLIENTE = 30  #@param {type:"number"}
INICIO_JORNADA = "07:00"  #@param {type:"string"}
JORNADA_MAXIMA_MIN = 480  #@param {type:"number"}
TIEMPO_LIMITE_POR_DIA_S = 30  #@param {type:"slider", min:5, max:120, step:5}
UMBRAL_ALERTA_PCT = 90  #@param {type:"number"}
UMBRAL_SUBUTILIZADO_PCT = 50  #@param {type:"number"}

parametros = ruteo.parametros_por_defecto(
    velocidad_kmh=float(VELOCIDAD_KMH), descarga_min=float(DESCARGA_MIN_POR_CLIENTE),
    inicio_jornada=str(INICIO_JORNADA).strip(), jornada_max_min=float(JORNADA_MAXIMA_MIN),
    tiempo_limite_s=float(TIEMPO_LIMITE_POR_DIA_S), umbral_alerta_pct=float(UMBRAL_ALERTA_PCT),
    umbral_subutilizado_pct=float(UMBRAL_SUBUTILIZADO_PCT),
)
fin = ruteo.minutos_a_hora(parametros["jornada_max_min"], parametros["inicio_jornada"])
print(f"Velocidad {VELOCIDAD_KMH} km/h · descarga {DESCARGA_MIN_POR_CLIENTE} min por cliente · "
      f"jornada {parametros['inicio_jornada']} a {fin} · cálculo hasta {TIEMPO_LIMITE_POR_DIA_S} s por día")
''')

    md("""
## 3. Cargar el archivo de Excel

Al ejecutar esta celda aparece el botón **Elegir archivos**. Seleccione un `.xlsx` de máximo 5 MB.
Si marca **USAR_ARCHIVO_DE_EJEMPLO**, se usa el archivo del caso, que trae Lunes a Jueves.
Antes de calcular se valida el archivo: si tiene errores, se listan con hoja, columna y nodo.
""")
    code('''
#@title 3. Cargar y validar el archivo { display-mode: "form" }
USAR_ARCHIVO_DE_EJEMPLO = False  #@param {type:"boolean"}
RUTA_LOCAL = ""  #@param {type:"string"}
#@markdown *RUTA_LOCAL solo se usa fuera de Colab (por ejemplo, en Jupyter en su computador).*

ARCHIVO_EJEMPLO = Path(ruteo.CONFIG["archivo_base"]).name
nombre_archivo, contenido = None, None
if USAR_ARCHIVO_DE_EJEMPLO:
    nombre_archivo = ARCHIVO_EJEMPLO
elif RUTA_LOCAL.strip():
    nombre_archivo, contenido = Path(RUTA_LOCAL.strip()).name, Path(RUTA_LOCAL.strip()).read_bytes()
elif EN_COLAB:
    subidos = _colab_files.upload()
    if subidos:
        nombre_archivo, contenido = next(iter(subidos.items()))
    else:
        print("No se seleccionó ningún archivo: se usa el archivo de ejemplo.")
        nombre_archivo = ARCHIVO_EJEMPLO
else:
    print("Fuera de Colab y sin RUTA_LOCAL: se usa el archivo de ejemplo.")
    nombre_archivo = ARCHIVO_EJEMPLO
if contenido is None:
    contenido = Path(ruteo.CONFIG["archivo_base"]).read_bytes()

VALIDO = False
try:
    if not nombre_archivo.lower().endswith(".xlsx"):
        raise ruteo.ErrorArchivo(f"«{nombre_archivo}» no es un libro de Excel .xlsx. Solo se aceptan archivos .xlsx.")
    datos, problemas = ruteo.preparar_datos(contenido, nombre_archivo, parametros)
except ruteo.ErrorArchivo as e:
    datos, problemas = None, [{"nivel": "error", "hoja": "", "columna": "", "nodo": "", "problema": str(e)}]

print(f"Archivo: {nombre_archivo}")
errores = [p for p in problemas if p["nivel"] == "error"]
if errores:
    print(f"✗ El archivo tiene {len(errores)} error(es). No se calcula. Corrija el archivo y ejecute de nuevo este paso.")
    display(vista_colab.tabla_problemas(errores))
else:
    VALIDO = True
    previo = ruteo.resumen_previo(datos)
    print(f"✓ Archivo válido. Flota: {previo['vehiculos_disponibles']} vehículos, {ruteo.formato_num(previo['capacidad_flota_t'], 1)} t · "
          + " · ".join(f"{t['cantidad']}× {t['codigo']} ({ruteo.formato_num(t['capacidad_t'], 1)} t, {ruteo.formato_cop(t['costo_km'])}/km)" for t in previo["flota"]))
    display(pd.DataFrame([{
        "Día": d["dia"],
        "Clientes con pedido": d.get("clientes", "—"),
        "Toneladas": ruteo.formato_num(d["toneladas"], 1) if d["con_datos"] else "—",
        "% de la flota": f"{ruteo.formato_num(d['uso_pct'], 0)} %" if d["con_datos"] else "—",
        "Observación": ("⚠ Supera la capacidad de la flota" if d.get("excede_capacidad") else "") if d["con_datos"] else "Sin datos (se omite)",
    } for d in previo["dias"]]))
    avisos = [p for p in problemas if p["nivel"] != "error" and "Columna vacía" not in p["problema"]]
    for p in avisos:
        print("ℹ", p["problema"])
''')

    md("## 4. Calcular las rutas\n\nLos días se resuelven en paralelo y cada uno se informa al terminar.")
    code('''
#@title 4. Calcular { display-mode: "form" }
import time
resultados = None
if not VALIDO:
    print("No se calcula: corrija los errores del paso 3.")
else:
    dias_con_datos = [d for d in datos.dias if datos.demanda.get(d) is not None]
    print(f"Resolviendo {len(dias_con_datos)} día(s): {', '.join(dias_con_datos)} (hasta {TIEMPO_LIMITE_POR_DIA_S} s por día)…")
    _inicio = time.time()
    def _avance(dia, r, hechos, total):
        estado = {"resuelto": "✓ resuelto", "infactible": "⚠ infactible", "error": "✗ error"}.get(r["estado"], r["estado"])
        print(f"  {dia}: {estado} · {hechos} de {total} · {time.time() - _inicio:.0f} s")
    resultados = ruteo.resolver_archivo(contenido, nombre_archivo, parametros, progreso=_avance)
    print("\\nResultado:", resultados["mensaje_final"])
''')

    md("""
## 5. Resumen semanal

Esta sección permite ver qué día cuesta más por tonelada y qué tan ajustada queda la jornada (holgura frente a la hora límite).
""")
    code('''
#@title 5. Resumen semanal { display-mode: "form" }
if resultados:
    display(HTML(vista_colab.tabla_resumen_html(resultados)))
    import matplotlib.pyplot as plt
    vista_colab.grafico_costos(resultados); plt.show()
''')

    md("""
## 6. Detalle por día

Cada pestaña es un día. Contiene:
- **5 indicadores:** costo, distancia, vehículos, utilización y regreso más tardío.
- **Mapa:** las líneas tienen flechas de sentido. Al pasar el cursor sobre un cliente se ven su demanda, vehículo y ETA. Las rutas se encienden o apagan desde el selector del mapa.
- **Tabla por vehículo:** semáforo ▲ alerta desde 90 % y ▼ subutilizado bajo 50 %. Debajo se despliega la secuencia de paradas.

Los días infactibles muestran el diagnóstico en lugar de las rutas.
""")
    code('''
#@title 6. Detalle por día { display-mode: "form" }
if resultados:
    display(HTML(vista_colab.html_dias(resultados)))
''')

    md("""
## Verificación de restricciones

Antes de mostrar cualquier resultado, se recalcula todo desde la matriz de distancias original:
- visitas de cada cliente,
- inicio y fin en el depot,
- carga, duración y flota,
- costo tramo por tramo, en su sentido.
""")
    code('''
#@title Tabla de cumplimiento { display-mode: "form" }
if resultados:
    tabla = vista_colab.tabla_verificacion(resultados)
    print("Todas las restricciones se cumplen ✔" if (tabla["Cumple"] == "✔").all() else "⚠ Hay restricciones incumplidas")
    display(tabla)
''')

    md("""
## 7. Descargar resultados

- **rutas.xlsx**: una hoja de resumen y una hoja de paradas por día (vehículo, orden, cliente, dirección, ETA y carga).
- **Tablero HTML**: el tablero interactivo completo, con selector de día, mapa, tabla y modo claro/oscuro. Se abre con doble clic en cualquier navegador y se puede compartir por correo.
""")
    code('''
#@title 7. Descargar rutas.xlsx y el tablero { display-mode: "form" }
if resultados:
    Path("rutas.xlsx").write_bytes(ruteo.exportar_rutas_excel(resultados))
    nombre_tablero = "tablero_rutas_" + Path(nombre_archivo).stem.replace(" ", "_") + ".html"
    Path(nombre_tablero).write_text(ruteo.exportar_tablero_html(resultados, "static"), encoding="utf-8")
    if EN_COLAB:
        _colab_files.download("rutas.xlsx")
        _colab_files.download(nombre_tablero)
        print("✓ Descargas iniciadas. Si el navegador las bloquea, búsquelas en el panel de archivos (ícono de carpeta, a la izquierda).")
    else:
        print(f"✓ Archivos guardados en {Path.cwd()}: rutas.xlsx y {nombre_tablero}")
''')

    md("""
## Vista previa del tablero (opcional)

Esta celda muestra aquí mismo el tablero que se descarga en el paso 7. Para verlo a pantalla completa, abra el archivo HTML descargado.
""")
    code('''
#@title Mostrar el tablero dentro del notebook { display-mode: "form" }
import html as _html, warnings
warnings.filterwarnings("ignore", message="Consider using IPython.display.IFrame")
if resultados:
    _tablero = ruteo.exportar_tablero_html(resultados, "static")
    display(HTML(f'<iframe srcdoc="{_html.escape(_tablero)}" style="width:100%;height:780px;border:1px solid #e1e4ea;border-radius:10px"></iframe>'))
''')

    md("""
---
### Método (resumen)

- **Modelo:** problema de ruteo con flota heterogénea (HFVRP), resuelto con **Google OR-Tools**. Cada vehículo tiene su propio costo por km, más una restricción de capacidad y otra de jornada (viaje + descarga + regreso ≤ 480 min). La matriz de distancias se usa tal como viene, asimétrica.
- **Búsqueda:** se prueban 5 estrategias de solución inicial y la mejor se mejora con *Guided Local Search*. Si ninguna encuentra un plan factible, hay dos respaldos: un modelo con penalización por cliente omitido y una asignación previa tipo *bin packing* con CP-SAT.
- **Días infactibles:** se informa la causa con cifras (demanda mayor que la flota, pedidos que no caben sin dividirse o jornada insuficiente) y no se entrega un plan parcial.
- **Supuestos:**
  - todos los vehículos salen a la hora de inicio, sin ventanas horarias;
  - cada vehículo hace una vuelta por día;
  - solo hay costo variable por km;
  - el trazado del mapa es en línea recta entre paradas, como referencia.
""")

    nb["cells"] = celdas
    nb["metadata"].update({
        "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
        "language_info": {"name": "python"},
        "colab": {"provenance": [], "name": "ruteo_colab.ipynb", "toc_visible": True},
    })
    nbf.write(nb, RAIZ / "ruteo_colab.ipynb")
    print("ruteo_colab.ipynb generado")


if __name__ == "__main__":
    construir()
