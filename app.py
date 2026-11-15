"""
app.py — Aplicación web (FastAPI) del tablero de ruteo.

Inicio:  python app.py   (abre el navegador en http://127.0.0.1:8000)

Puntos de acceso:
- GET  /                  tablero (static/index.html)
- GET  /api/inicial       resultado del archivo de ejemplo (o trabajo en curso)
- POST /api/cargar        recibe un .xlsx, valida y lanza la solución en segundo plano
- GET  /api/trabajo/{id}  progreso y resultados parciales de un trabajo
- GET  /api/plantilla     plantilla de carga (.xlsx)
- POST /api/rutas.xlsx    exporta rutas.xlsx a partir de los resultados actuales
- POST /api/exportar      genera un HTML autocontenido con el tablero actual

Los archivos subidos se procesan en memoria; no se guardan en disco.
Todo el algoritmo vive en ruteo.py (no se duplica lógica aquí).
"""

from __future__ import annotations

import hashlib
import json
import logging
import multiprocessing
import os
import re
import socket
import threading
import uuid
import webbrowser
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import ruteo

DIRECTORIO = Path(__file__).resolve().parent
ESTATICOS = DIRECTORIO / "static"
CACHE_BASE = DIRECTORIO / "salidas" / ".cache_resultado_base.json"
HOST = os.environ.get("RUTEO_HOST", "127.0.0.1")
PUERTO = int(os.environ.get("RUTEO_PUERTO", "8000"))
MAX_TRABAJOS = 20

registro = logging.getLogger("ruteo.app")


# =============================================================================
# Trabajos en segundo plano
# =============================================================================


class Trabajo:
    """Un cálculo en curso: resultados parciales que se completan día a día."""

    def __init__(self, resultados: dict, dias_a_resolver: list, al_terminar=None):
        self.id = uuid.uuid4().hex[:12]
        self.resultados = resultados
        self.pendientes = list(dias_a_resolver)
        self.en_curso: list = []
        self.futuros: dict = {}
        self.total = len(dias_a_resolver)
        self.hechos = 0
        self.candado = threading.Lock()
        self.al_terminar = al_terminar
        if self.total == 0:
            self._cerrar()

    @property
    def terminado(self) -> bool:
        return self.hechos >= self.total

    def registrar(self, dia: str, resultado: dict):
        """Guarda el resultado de un día (llamado desde el hilo del ejecutor)."""
        with self.candado:
            self.resultados["dias"] = [resultado if d["dia"] == dia else d for d in self.resultados["dias"]]
            if dia in self.en_curso:
                self.en_curso.remove(dia)
            self.hechos += 1
            if self.terminado:
                self._cerrar()

    def _cerrar(self):
        self.resultados["mensaje_final"] = ruteo.resumen_final(self.resultados)
        self.resultados["fecha_calculo"] = datetime.now().isoformat(timespec="seconds")
        if self.al_terminar:
            try:
                self.al_terminar(self.resultados)
            except Exception:  # la caché es opcional
                registro.exception("No se pudo guardar la caché")

    def estado(self) -> dict:
        """Instantánea serializable del trabajo."""
        with self.candado:
            corriendo = [d for d in self.en_curso if d in self.futuros and self.futuros[d].running()]
            return {"trabajo": self.id, "estado": "terminado" if self.terminado else "en_curso",
                    "hechos": self.hechos, "total": self.total, "en_curso": corriendo or list(self.en_curso[:1]),
                    "resultados": json.loads(json.dumps(self.resultados))}


ESTADO: dict = {"ejecutor": None, "trabajos": {}, "inicial": None}


def lanzar_trabajo(datos: ruteo.DatosRuteo, problemas: list, parametros: dict, al_terminar=None) -> Trabajo:
    """Crea un trabajo y envía cada día con datos al ejecutor de procesos (en paralelo)."""
    resultados = ruteo.encabezado_resultados(datos, parametros, problemas)
    dias = [d for d in datos.dias if datos.demanda.get(d) is not None]
    trabajo = Trabajo(resultados, dias, al_terminar)
    trabajos = ESTADO["trabajos"]
    trabajos[trabajo.id] = trabajo
    while len(trabajos) > MAX_TRABAJOS:
        trabajos.pop(next(iter(trabajos)))
    for dia in dias:
        trabajo.en_curso.append(dia)
        futuro = ESTADO["ejecutor"].submit(ruteo.resolver_dia, datos, dia, parametros)
        trabajo.futuros[dia] = futuro
        futuro.add_done_callback(lambda f, d=dia: trabajo.registrar(d, _resultado_seguro(f, d)))
    return trabajo


def _resultado_seguro(futuro, dia: str) -> dict:
    """Resultado de un día; si el proceso falló, un error comprensible (sin trazas)."""
    try:
        return futuro.result()
    except Exception as e:
        registro.exception("Fallo al resolver %s", dia)
        return {"dia": dia, "estado": "error", "mensaje": f"No fue posible calcular {dia} ({type(e).__name__})."}


def _huella_base(parametros: dict) -> str:
    """Huella del archivo base + algoritmo + parámetros, para reutilizar el resultado guardado."""
    contenido = Path(parametros["archivo_base"]).read_bytes() + Path(ruteo.__file__).read_bytes()
    config = json.dumps({k: v for k, v in parametros.items() if k != "archivo_base"}, sort_keys=True, default=str)
    return hashlib.sha256(contenido + config.encode()).hexdigest()


def iniciar_base():
    """Resuelve el archivo de ejemplo al arrancar (o lo toma de la caché si no cambió)."""
    parametros = ruteo.CONFIG
    huella = _huella_base(parametros)
    if CACHE_BASE.exists():
        try:
            guardado = json.loads(CACHE_BASE.read_text(encoding="utf-8"))
            if guardado.get("huella") == huella:
                ESTADO["inicial"] = {"resultados": guardado["resultados"]}
                return
        except Exception:
            pass
    datos, problemas = ruteo.preparar_datos(parametros["archivo_base"], Path(parametros["archivo_base"]).name, parametros)

    def guardar(resultados):
        CACHE_BASE.parent.mkdir(exist_ok=True)
        CACHE_BASE.write_text(json.dumps({"huella": huella, "resultados": resultados}, ensure_ascii=False), encoding="utf-8")
        ESTADO["inicial"] = {"resultados": resultados}

    trabajo = lanzar_trabajo(datos, problemas, parametros, guardar)
    ESTADO["inicial"] = {"trabajo": trabajo.id}


@asynccontextmanager
async def ciclo_de_vida(_app: FastAPI):
    """Crea el ejecutor de procesos al iniciar y lo cierra al terminar."""
    procesos = ruteo._numero_procesos(ruteo.CONFIG, os.cpu_count() or 1)
    # "spawn": los procesos de cálculo no heredan el socket del servidor (igual en Windows, macOS y Linux).
    ESTADO["ejecutor"] = ProcessPoolExecutor(max_workers=procesos, mp_context=multiprocessing.get_context("spawn"))
    iniciar_base()
    yield
    ESTADO["ejecutor"].shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Tablero de ruteo", lifespan=ciclo_de_vida, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


# =============================================================================
# Manejo de errores: siempre en español, nunca trazas
# =============================================================================


@app.exception_handler(Exception)
async def error_inesperado(_request: Request, exc: Exception):
    registro.exception("Error inesperado", exc_info=exc)
    return JSONResponse(status_code=500, content={"mensaje": "Ocurrió un error inesperado en el servidor. "
                                                             "Intente de nuevo; si persiste, revise el archivo cargado."})


@app.exception_handler(RequestValidationError)
async def error_solicitud(_request: Request, _exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"mensaje": "La solicitud no es válida. Seleccione un archivo .xlsx e intente de nuevo."})


@app.exception_handler(StarletteHTTPException)
async def error_http(_request: Request, exc: StarletteHTTPException):
    mensajes = {404: "Recurso no encontrado.", 405: "Operación no permitida."}
    return JSONResponse(status_code=exc.status_code, content={"mensaje": mensajes.get(exc.status_code, "Solicitud no válida.")})


# =============================================================================
# Rutas
# =============================================================================


@app.get("/", response_class=HTMLResponse)
async def tablero():
    return FileResponse(ESTATICOS / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/inicial")
async def inicial():
    """Resultado del archivo de ejemplo, o el trabajo que lo está calculando."""
    info = ESTADO["inicial"] or {}
    if "trabajo" in info and info["trabajo"] in ESTADO["trabajos"]:
        return ESTADO["trabajos"][info["trabajo"]].estado()
    return {"trabajo": None, "estado": "terminado", "resultados": info.get("resultados")}


@app.post("/api/cargar")
async def cargar(archivo: UploadFile = File(...)):
    """Valida el archivo en memoria. Si es válido, lanza la solución en segundo plano."""
    nombre = Path(archivo.filename or "archivo").name
    limite = int(ruteo.CONFIG["tamano_max_mb"] * 1024 * 1024)
    if not nombre.lower().endswith(".xlsx"):
        return _invalido(nombre, f"«{nombre}» no es un libro de Excel .xlsx. Solo se aceptan archivos .xlsx.")
    contenido = await archivo.read(limite + 1)
    if len(contenido) > limite:
        return _invalido(nombre, f"El archivo supera el tamaño máximo de {ruteo.formato_num(ruteo.CONFIG['tamano_max_mb'], 0)} MB.")
    try:
        datos, problemas = ruteo.preparar_datos(contenido, nombre, ruteo.CONFIG)
    except ruteo.ErrorArchivo as e:
        return _invalido(nombre, str(e))
    if datos is None:
        return {"ok": False, "archivo": nombre, "errores": [p for p in problemas if p["nivel"] == "error"]}
    trabajo = lanzar_trabajo(datos, problemas, ruteo.CONFIG)
    return {"ok": True, **trabajo.estado()}


def _invalido(nombre: str, mensaje: str) -> dict:
    """Respuesta de archivo rechazado con un único problema general."""
    return {"ok": False, "archivo": nombre, "errores": [{"nivel": "error", "hoja": "", "columna": "", "nodo": "", "problema": mensaje}]}


@app.get("/api/trabajo/{id_trabajo}")
async def consultar_trabajo(id_trabajo: str):
    trabajo = ESTADO["trabajos"].get(id_trabajo)
    if trabajo is None:
        return JSONResponse(status_code=404, content={"mensaje": "El cálculo ya no está disponible. Cargue el archivo de nuevo."})
    return trabajo.estado()


@app.get("/api/plantilla")
async def plantilla():
    contenido = ruteo.generar_plantilla(ruteo.CONFIG)
    return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="plantilla_demanda.xlsx"'})


@app.post("/api/rutas.xlsx")
async def descargar_rutas(request: Request):
    resultados = await _leer_resultados(request)
    if resultados is None:
        return JSONResponse(status_code=400, content={"mensaje": "No hay resultados para exportar."})
    contenido = ruteo.exportar_rutas_excel(resultados)
    return Response(contenido, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="rutas.xlsx"'})


@app.post("/api/exportar")
async def exportar(request: Request):
    resultados = await _leer_resultados(request)
    if resultados is None:
        return JSONResponse(status_code=400, content={"mensaje": "No hay resultados para exportar."})
    nombre = re.sub(r"[^\w\-]+", "_", Path(resultados.get("archivo", "tablero")).stem)[:40]
    return Response(html_autocontenido(resultados), media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="tablero_rutas_{nombre}.html"'})


async def _leer_resultados(request: Request):
    """Lee el JSON de resultados enviado por el tablero."""
    try:
        cuerpo = await request.json()
    except Exception:
        return None
    return cuerpo if isinstance(cuerpo, dict) and isinstance(cuerpo.get("dias"), list) else None


def html_autocontenido(resultados: dict) -> str:
    """
    HTML de un solo archivo: CSS y JavaScript en línea (incluye Leaflet y Chart.js) y los
    resultados incrustados. Solo el mapa base requiere internet.
    """
    html = (ESTATICOS / "index.html").read_text(encoding="utf-8")

    def estilo(m):
        return f"<style>\n{(ESTATICOS / m.group(1)).read_text(encoding='utf-8')}\n</style>"

    def guion(m):
        codigo = (ESTATICOS / m.group(1)).read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f"<script>\n{codigo}\n</script>"

    html = re.sub(r'<link rel="stylesheet" href="/static/([^"]+)">', estilo, html)
    datos = json.dumps(resultados, ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("<!--DATOS_EXPORTADOS-->", f"<script>window.DATOS_EXPORTADOS = {datos};</script>")
    html = re.sub(r'<script src="/static/([^"]+)"></script>', guion, html)
    return html


# =============================================================================
# Arranque
# =============================================================================


def _puerto_libre(preferido: int) -> int:
    """El puerto preferido si está libre; si no, el siguiente disponible."""
    for puerto in range(preferido, preferido + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((HOST, puerto)) != 0:
                return puerto
    return preferido


def main():
    puerto = _puerto_libre(PUERTO)
    url = f"http://{HOST}:{puerto}"
    print(f"\n  Tablero de ruteo disponible en {url}\n  (Ctrl+C para detener)\n")
    if os.environ.get("RUTEO_NO_ABRIR") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=puerto, log_level="warning")


if __name__ == "__main__":
    main()
