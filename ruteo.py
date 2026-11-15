"""
ruteo.py — Ruteo de distribución urbana con flota heterogénea (HFVRP).

Un único módulo con todo el algoritmo, compartido por el notebook y la aplicación web:

1. Configuración (``CONFIG``).
2. Lectura y validación del libro de Excel (Clientes, Matriz_Distancias, Demanda, Vehiculos).
3. Preparación de matrices enteras para OR-Tools.
4. Solver (OR-Tools Routing + respaldo de asignación tipo bin packing con CP-SAT).
5. Diagnóstico de días infactibles.
6. Verificación independiente de restricciones.
7. Indicadores para el tablero y exportes (rutas.xlsx, plantilla).

Funciones principales:
- ``resolver_ruteo(demanda_dia, matriz, flota, parametros) -> dict``
- ``resolver_archivo(ruta_o_bytes) -> dict`` (serializable a JSON)
"""

from __future__ import annotations

import copy
import io
import math
import os
import re
import time
import unicodedata
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from ortools.sat.python import cp_model

# =============================================================================
# 1. CONFIGURACIÓN (único bloque de parámetros, compartido por notebook y app)
# =============================================================================

DIRECTORIO = Path(__file__).resolve().parent

CONFIG: dict = {
    # --- Operación ---
    "velocidad_kmh": 24.0,           # velocidad promedio urbana
    "descarga_min": 30.0,            # tiempo de descarga por cliente
    "inicio_jornada": "07:00",       # hora de salida del depot
    "jornada_max_min": 480.0,        # 8 h: viaje + descargas + regreso
    "nodo_depot": 0,
    # --- Solver ---
    "tiempo_limite_s": 30.0,         # tiempo máximo de cálculo por día
    "estrategias_iniciales": [       # se prueban todas y se parte de la mejor
        "PATH_CHEAPEST_ARC",
        "PARALLEL_CHEAPEST_INSERTION",
        "SAVINGS",
        "CHRISTOFIDES",
        "LOCAL_CHEAPEST_INSERTION",
    ],
    "metaheuristica": "GUIDED_LOCAL_SEARCH",
    "fraccion_fase_inicial": 0.25,   # parte del tiempo usada para soluciones iniciales
    "tiempo_empaque_s": 10.0,        # límite para el respaldo CP-SAT (bin packing)
    "procesos_paralelos": None,      # None = según núcleos disponibles
    # --- Escalas enteras para OR-Tools (sin pérdida de precisión) ---
    "escala_distancia": 1000,        # km -> metros
    "escala_demanda": 1000,          # t  -> kg
    # --- Tablero ---
    "umbral_alerta_pct": 90.0,       # semáforo: alerta desde este % (capacidad y jornada)
    "umbral_subutilizado_pct": 50.0, # capacidad usada por debajo de este % = subutilizado
    # --- Archivos ---
    "tamano_max_mb": 5.0,
    "archivo_base": str(DIRECTORIO / "datos" / "Reto_VRP_Medellin_Datos.xlsx"),
    "hojas": {
        "clientes": "Clientes",
        "matriz": "Matriz_Distancias",
        "demanda": "Demanda",
        "vehiculos": "Vehiculos",
    },
}


def parametros_por_defecto(**cambios) -> dict:
    """Devuelve una copia de CONFIG con los cambios indicados (p. ej. tiempo_limite_s=10)."""
    parametros = copy.deepcopy(CONFIG)
    parametros.update(cambios)
    return parametros


# =============================================================================
# 2. ESTRUCTURAS DE DATOS
# =============================================================================


class ErrorArchivo(Exception):
    """Error al abrir el archivo (no es Excel, está dañado, es muy grande...)."""


@dataclass
class Matriz:
    """Matriz de distancias viales (km). ``km[i][j]`` = distancia de nodos[i] a nodos[j]."""

    nodos: list
    km: np.ndarray

    def posicion(self, nodo: int) -> int:
        """Posición del nodo dentro de la matriz."""
        return self._indice[nodo]

    def __post_init__(self):
        self._indice = {n: i for i, n in enumerate(self.nodos)}

    def distancia(self, origen: int, destino: int) -> float:
        """Distancia en km del nodo origen al nodo destino (respeta el sentido)."""
        return float(self.km[self._indice[origen], self._indice[destino]])


@dataclass
class TipoVehiculo:
    """Un tipo de vehículo de la flota."""

    tipo: str            # nombre como viene en el archivo, p. ej. "Camión Tipo A"
    codigo: str          # código corto, p. ej. "A"
    cantidad: int
    capacidad_t: float
    costo_km: float


@dataclass
class DatosRuteo:
    """Datos completos y validados de un libro."""

    clientes: list                  # [{nodo, nombre, direccion, lat, lon}]
    matriz: Matriz
    flota: list                     # [TipoVehiculo]
    dias: list                      # nombres de días en el orden del archivo
    demanda: dict                   # dia -> {nodo: t} (solo > 0) o None si el día no tiene datos
    archivo: str = ""
    usa_datos_base: bool = False
    advertencias: list = field(default_factory=list)


# =============================================================================
# 3. UTILIDADES DE TEXTO, NÚMEROS Y HORAS
# =============================================================================


def normalizar(texto) -> str:
    """Minúsculas, sin tildes y sin espacios repetidos (para comparar encabezados)."""
    if texto is None or (isinstance(texto, float) and math.isnan(texto)):
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[\s_]+", " ", texto).strip().lower()


def es_vacio(valor) -> bool:
    """True si la celda está vacía (None, NaN o texto en blanco)."""
    if valor is None:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    return isinstance(valor, str) and valor.strip() == ""


def numero_desde_texto(valor) -> float:
    """
    Extrae un número de un texto con unidades, en formato colombiano.

    "10 toneladas" -> 10 ; "4.200 COP/km" -> 4200 ; "2,5 t" -> 2.5.
    El punto seguido de exactamente tres dígitos es separador de miles.
    Lanza ValueError si no hay número.
    """
    if isinstance(valor, (int, float, np.integer, np.floating)) and not isinstance(valor, bool):
        if math.isnan(float(valor)):
            raise ValueError("vacío")
        return float(valor)
    texto = str(valor).strip()
    coincidencia = re.search(r"-?\d[\d.,]*", texto)
    if not coincidencia:
        raise ValueError(f"sin número en «{texto}»")
    numero = coincidencia.group(0).rstrip(".,")
    if "," in numero:
        numero = numero.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", numero):
        numero = numero.replace(".", "")
    return float(numero)


def numero_estricto(valor):
    """
    Convierte una celda de demanda a número. Vacío -> None.
    Acepta números o textos puramente numéricos ("1,2" o "1.2"). Otro texto -> ValueError.
    """
    if es_vacio(valor):
        return None
    if isinstance(valor, bool):
        raise ValueError("valor lógico")
    if isinstance(valor, (int, float, np.integer, np.floating)):
        return float(valor)
    texto = str(valor).strip()
    if re.fullmatch(r"-?\d+([.,]\d+)?", texto):
        return float(texto.replace(",", "."))
    raise ValueError(f"«{texto}» no es un número")


def minutos_a_hora(minutos_desde_inicio: float, inicio: str) -> str:
    """Convierte minutos desde el inicio de jornada a hora HH:MM (24 h)."""
    h, m = (int(x) for x in inicio.split(":"))
    total = h * 60 + m + int(round(minutos_desde_inicio))
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def nodo_desde_rotulo(rotulo):
    """Extrae el número de nodo de un rótulo tipo "12 - D1 Belén Rosales". None si no hay."""
    if isinstance(rotulo, (int, np.integer)):
        return int(rotulo)
    if isinstance(rotulo, (float, np.floating)) and not math.isnan(rotulo) and float(rotulo).is_integer():
        return int(rotulo)
    coincidencia = re.match(r"^\s*(\d+)", str(rotulo))
    return int(coincidencia.group(1)) if coincidencia else None


def _a_python(valor):
    """Convierte tipos numpy a tipos nativos para JSON."""
    if isinstance(valor, dict):
        return {str(k): _a_python(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_a_python(v) for v in valor]
    if isinstance(valor, np.integer):
        return int(valor)
    if isinstance(valor, np.floating):
        return float(valor)
    if isinstance(valor, np.bool_):
        return bool(valor)
    return valor


# =============================================================================
# 4. LECTURA Y VALIDACIÓN DEL EXCEL
# =============================================================================


def _problema(hoja, columna, nodo, problema, nivel="error") -> dict:
    """Crea un registro de validación (hoja, columna, nodo, problema)."""
    return {"nivel": nivel, "hoja": hoja or "", "columna": "" if columna is None else str(columna),
            "nodo": "" if nodo is None else str(nodo), "problema": problema}


def leer_libro(fuente, parametros: dict | None = None) -> dict:
    """
    Abre un .xlsx desde ruta o bytes y devuelve {nombre_hoja_normalizado: DataFrame sin encabezado}.
    Lanza ErrorArchivo con un mensaje en español si no se puede leer.
    """
    parametros = parametros or CONFIG
    limite = parametros["tamano_max_mb"] * 1024 * 1024
    if isinstance(fuente, (bytes, bytearray)):
        contenido = bytes(fuente)
    else:
        ruta = Path(fuente)
        if not ruta.exists():
            raise ErrorArchivo(f"No se encontró el archivo «{ruta.name}».")
        contenido = ruta.read_bytes()
    if len(contenido) > limite:
        raise ErrorArchivo(f"El archivo pesa {len(contenido) / 1048576:.1f} MB; el máximo permitido es "
                           f"{parametros['tamano_max_mb']:.0f} MB.".replace(".", ","))
    if not contenido.startswith(b"PK"):
        raise ErrorArchivo("El archivo no es un libro de Excel .xlsx válido. Guárdelo como «Libro de Excel (.xlsx)» y vuelva a cargarlo.")
    try:
        hojas = pd.read_excel(io.BytesIO(contenido), sheet_name=None, header=None, engine="openpyxl")
    except Exception:
        raise ErrorArchivo("No fue posible leer el libro de Excel. Verifique que no esté dañado ni protegido con contraseña.")
    return {normalizar(nombre): (nombre, df) for nombre, df in hojas.items()}


def _tabla_con_encabezado(df: pd.DataFrame) -> pd.DataFrame:
    """Usa la primera fila no vacía como encabezado y elimina filas/columnas totalmente vacías."""
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.empty:
        return df
    encabezado = [("" if es_vacio(v) else str(v).strip()) for v in df.iloc[0]]
    cuerpo = df.iloc[1:].reset_index(drop=True)
    cuerpo.columns = encabezado
    return cuerpo


def _buscar_columna(columnas, prefijos) -> str | None:
    """Encuentra la columna cuyo nombre normalizado empieza por alguno de los prefijos."""
    for col in columnas:
        nombre = normalizar(col)
        if any(nombre.startswith(p) for p in prefijos):
            return col
    return None


def leer_clientes(df, nombre_hoja, problemas) -> list:
    """Lee la hoja Clientes. Registra problemas y devuelve la lista de clientes válidos."""
    tabla = _tabla_con_encabezado(df)
    requeridas = {"Nodo": ["nodo"], "Cliente": ["cliente", "nombre"], "Dirección": ["direcc"],
                  "Latitud": ["lat"], "Longitud": ["lon"]}
    columnas = {}
    for etiqueta, prefijos in requeridas.items():
        col = _buscar_columna(tabla.columns, prefijos)
        if col is None:
            problemas.append(_problema(nombre_hoja, etiqueta, None, f"Falta la columna «{etiqueta}»."))
        columnas[etiqueta] = col
    if columnas["Nodo"] is None:
        return []
    clientes, vistos = [], set()
    for i, fila in tabla.iterrows():
        nodo = nodo_desde_rotulo(fila[columnas["Nodo"]])
        if nodo is None:
            if not es_vacio(fila[columnas["Nodo"]]):
                problemas.append(_problema(nombre_hoja, "Nodo", fila[columnas["Nodo"]], f"Fila {i + 2}: el nodo no es un número entero."))
            continue
        if nodo in vistos:
            problemas.append(_problema(nombre_hoja, "Nodo", nodo, "Nodo repetido."))
            continue
        vistos.add(nodo)
        coordenadas = {}
        for etiqueta, limite in (("Latitud", 90), ("Longitud", 180)):
            if columnas[etiqueta] is None:
                coordenadas[etiqueta] = None
                continue
            try:
                valor = numero_estricto(fila[columnas[etiqueta]])
                if valor is None:
                    raise ValueError
                if abs(valor) > limite:
                    problemas.append(_problema(nombre_hoja, etiqueta, nodo, f"{etiqueta} {formato_num(valor, 6)} fuera de rango (±{limite}°)."))
                coordenadas[etiqueta] = valor
            except ValueError:
                problemas.append(_problema(nombre_hoja, etiqueta, nodo, f"{etiqueta} vacía o no numérica."))
                coordenadas[etiqueta] = None
        texto = lambda etiqueta: ("" if columnas[etiqueta] is None or es_vacio(fila[columnas[etiqueta]])
                                  else str(fila[columnas[etiqueta]]).strip())
        clientes.append({
            "nodo": nodo,
            "nombre": texto("Cliente"),
            "direccion": texto("Dirección"),
            "lat": coordenadas["Latitud"], "lon": coordenadas["Longitud"],
        })
    if not clientes:
        problemas.append(_problema(nombre_hoja, None, None, "La hoja no tiene clientes."))
    return clientes


def leer_matriz(df, nombre_hoja, problemas) -> Matriz | None:
    """Lee la matriz de distancias (rótulos "N - Nombre"). Valida forma, rótulos y valores."""
    df = df.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    df.columns = range(df.shape[1])
    if df.shape[0] < 2 or df.shape[1] < 2:
        problemas.append(_problema(nombre_hoja, None, None, "La matriz está vacía."))
        return None
    rotulos_col = [nodo_desde_rotulo(v) for v in df.iloc[0, 1:]]
    rotulos_fil = [nodo_desde_rotulo(v) for v in df.iloc[1:, 0]]
    errores_antes = len(problemas)
    for j, nodo in enumerate(rotulos_col):
        if nodo is None:
            problemas.append(_problema(nombre_hoja, f"columna {j + 2}", None, f"Rótulo de columna «{df.iloc[0, j + 1]}» sin número de nodo."))
    for i, nodo in enumerate(rotulos_fil):
        if nodo is None:
            problemas.append(_problema(nombre_hoja, "A", None, f"Fila {i + 2}: rótulo «{df.iloc[i + 1, 0]}» sin número de nodo."))
    if len(rotulos_col) != len(rotulos_fil):
        problemas.append(_problema(nombre_hoja, None, None,
                                   f"La matriz no es cuadrada: {len(rotulos_fil)} filas y {len(rotulos_col)} columnas."))
    for eje, rotulos in (("filas", rotulos_fil), ("columnas", rotulos_col)):
        repetidos = sorted({n for n in rotulos if n is not None and rotulos.count(n) > 1})
        for n in repetidos:
            problemas.append(_problema(nombre_hoja, None, n, f"Nodo repetido en las {eje}."))
    if len(problemas) > errores_antes:
        return None
    if set(rotulos_col) != set(rotulos_fil):
        for n in sorted(set(rotulos_col) ^ set(rotulos_fil)):
            problemas.append(_problema(nombre_hoja, None, n, "El nodo aparece en filas o columnas, pero no en ambas."))
        return None
    valores = np.full((len(rotulos_fil), len(rotulos_col)), np.nan)
    for i in range(len(rotulos_fil)):
        for j in range(len(rotulos_col)):
            celda = df.iloc[i + 1, j + 1]
            try:
                v = numero_estricto(celda)
            except ValueError:
                problemas.append(_problema(nombre_hoja, f"destino {rotulos_col[j]}", rotulos_fil[i],
                                           f"Distancia no numérica de {rotulos_fil[i]} a {rotulos_col[j]}: «{celda}»."))
                continue
            if v is None:
                problemas.append(_problema(nombre_hoja, f"destino {rotulos_col[j]}", rotulos_fil[i],
                                           f"Celda vacía: falta la distancia de {rotulos_fil[i]} a {rotulos_col[j]}."))
            elif v < 0:
                problemas.append(_problema(nombre_hoja, f"destino {rotulos_col[j]}", rotulos_fil[i],
                                           f"Distancia negativa de {rotulos_fil[i]} a {rotulos_col[j]} ({formato_num(v, 1)})."))
            else:
                valores[i, j] = v
    if len(problemas) > errores_antes:
        return None
    # Reordenar para que filas y columnas sigan el mismo orden de nodos.
    orden = sorted(rotulos_fil)
    pos_f = {n: i for i, n in enumerate(rotulos_fil)}
    pos_c = {n: j for j, n in enumerate(rotulos_col)}
    km = np.array([[valores[pos_f[a], pos_c[b]] for b in orden] for a in orden], dtype=float)
    return Matriz(nodos=orden, km=km)


def leer_vehiculos(df, nombre_hoja, problemas) -> list:
    """Lee la flota. Capacidad y costo pueden venir como texto ("10 toneladas", "4.200 COP/km")."""
    tabla = _tabla_con_encabezado(df)
    requeridas = {"Tipo de Vehículo": ["tipo"], "Cantidad": ["cantidad"], "Capacidad": ["capacidad"],
                  "Costo Variable": ["costo"]}
    columnas = {}
    for etiqueta, prefijos in requeridas.items():
        col = _buscar_columna(tabla.columns, prefijos)
        if col is None:
            problemas.append(_problema(nombre_hoja, etiqueta, None, f"Falta la columna «{etiqueta}»."))
        columnas[etiqueta] = col
    if any(c is None for c in columnas.values()):
        return []
    flota, codigos = [], set()
    for i, fila in tabla.iterrows():
        nombre = fila[columnas["Tipo de Vehículo"]]
        if es_vacio(nombre):
            continue
        nombre = str(nombre).strip()
        valores = {}
        for etiqueta in ("Cantidad", "Capacidad", "Costo Variable"):
            try:
                valores[etiqueta] = numero_desde_texto(fila[columnas[etiqueta]])
            except ValueError:
                problemas.append(_problema(nombre_hoja, etiqueta, None, f"{nombre}: «{fila[columnas[etiqueta]]}» no tiene un número válido."))
        if len(valores) < 3:
            continue
        if valores["Cantidad"] < 0 or not float(valores["Cantidad"]).is_integer():
            problemas.append(_problema(nombre_hoja, "Cantidad", None, f"{nombre}: la cantidad debe ser un entero ≥ 0."))
            continue
        if valores["Capacidad"] <= 0:
            problemas.append(_problema(nombre_hoja, "Capacidad", None, f"{nombre}: la capacidad debe ser mayor que 0."))
            continue
        if valores["Costo Variable"] < 0:
            problemas.append(_problema(nombre_hoja, "Costo Variable", None, f"{nombre}: el costo no puede ser negativo."))
            continue
        codigo = _codigo_vehiculo(nombre, codigos, i)
        codigos.add(codigo)
        flota.append(TipoVehiculo(tipo=nombre, codigo=codigo, cantidad=int(valores["Cantidad"]),
                                  capacidad_t=valores["Capacidad"], costo_km=valores["Costo Variable"]))
    if not flota or sum(t.cantidad for t in flota) == 0:
        problemas.append(_problema(nombre_hoja, None, None, "No hay vehículos disponibles en la flota."))
    return flota


def _codigo_vehiculo(nombre: str, usados: set, i: int) -> str:
    """Código corto del tipo: la palabra que sigue a "Tipo" (p. ej. "A"), o la última palabra."""
    coincidencia = re.search(r"tipo\s+(\S+)", nombre, flags=re.IGNORECASE)
    codigo = coincidencia.group(1) if coincidencia else nombre.split()[-1]
    codigo = re.sub(r"[^\wÁÉÍÓÚÑáéíóúñ]", "", codigo)[:6] or f"T{i + 1}"
    base, k = codigo, 2
    while codigo in usados:
        codigo, k = f"{base}{k}", k + 1
    return codigo


def leer_demanda(df, nombre_hoja, problemas) -> tuple[list, dict]:
    """
    Lee la hoja Demanda. Los días son las columnas que existan después de «Cliente».
    Devuelve (dias, demanda) con demanda[dia] = {nodo: t>0} o None si la columna está vacía.
    """
    tabla = _tabla_con_encabezado(df)
    col_nodo = _buscar_columna(tabla.columns, ["nodo"])
    col_cliente = _buscar_columna(tabla.columns, ["cliente"])
    if col_nodo is None:
        problemas.append(_problema(nombre_hoja, "Nodo", None, "Falta la columna «Nodo»."))
    if col_cliente is None:
        problemas.append(_problema(nombre_hoja, "Cliente", None, "Falta la columna «Cliente» (los días se leen a su derecha)."))
    if col_nodo is None or col_cliente is None:
        return [], {}
    columnas = list(tabla.columns)
    columnas_dia = columnas[columnas.index(col_cliente) + 1:]
    dias = []
    for col in columnas_dia:
        if col == "":
            if tabla[col].map(lambda v: not es_vacio(v)).any():
                problemas.append(_problema(nombre_hoja, "(sin nombre)", None, "Hay una columna con datos pero sin nombre de día."))
            continue
        if col in dias:
            problemas.append(_problema(nombre_hoja, col, None, "Día repetido."))
            continue
        dias.append(col)
    if not dias:
        problemas.append(_problema(nombre_hoja, None, None, "No hay columnas de días después de «Cliente»."))
        return [], {}
    demanda, vistos = {}, set()
    filas = []
    for i, fila in tabla.iterrows():
        nodo = nodo_desde_rotulo(fila[col_nodo])
        if nodo is None:
            if not es_vacio(fila[col_nodo]) or any(not es_vacio(fila[d]) for d in dias):
                problemas.append(_problema(nombre_hoja, "Nodo", fila[col_nodo], f"Fila {i + 2}: nodo vacío o no entero."))
            continue
        if nodo in vistos:
            problemas.append(_problema(nombre_hoja, "Nodo", nodo, "Nodo repetido."))
            continue
        vistos.add(nodo)
        filas.append((nodo, fila))
    for dia in dias:
        valores, alguno_numerico = {}, False
        for nodo, fila in filas:
            try:
                v = numero_estricto(fila[dia])
            except ValueError as e:
                problemas.append(_problema(nombre_hoja, dia, nodo, f"Demanda no numérica: {e}."))
                continue
            if v is None:
                continue
            alguno_numerico = True
            if v < 0:
                problemas.append(_problema(nombre_hoja, dia, nodo, f"Demanda negativa ({formato_num(v, 2)} t)."))
            elif v > 0:
                valores[nodo] = v
        demanda[dia] = valores if alguno_numerico else None
    return dias, demanda


def validar_cruces(datos: DatosRuteo, problemas: list, parametros: dict):
    """Validaciones entre hojas: nodos de Demanda/Clientes vs. matriz, depot, pedidos vs. capacidad."""
    hojas = parametros["hojas"]
    depot = parametros["nodo_depot"]
    nodos_clientes = {c["nodo"] for c in datos.clientes}
    nodos_matriz = set(datos.matriz.nodos)
    if depot not in nodos_clientes:
        problemas.append(_problema(hojas["clientes"], "Nodo", depot, "No existe el depot (nodo 0)."))
    if depot not in nodos_matriz:
        problemas.append(_problema(hojas["matriz"], None, depot, "La matriz no incluye el depot (nodo 0)."))
    for n in sorted(nodos_clientes - nodos_matriz):
        problemas.append(_problema(hojas["matriz"], None, n, "El cliente no tiene fila ni columna en la matriz de distancias."))
    capacidad_max = max((t.capacidad_t for t in datos.flota if t.cantidad > 0), default=0)
    for dia in datos.dias:
        for nodo, t in (datos.demanda.get(dia) or {}).items():
            if nodo == depot:
                problemas.append(_problema(hojas["demanda"], dia, nodo, "El depot no puede tener demanda."))
            elif nodo not in nodos_clientes:
                problemas.append(_problema(hojas["demanda"], dia, nodo, "El nodo no existe en la hoja Clientes."))
            elif nodo not in nodos_matriz:
                problemas.append(_problema(hojas["demanda"], dia, nodo, "El nodo no existe en la matriz de distancias."))
            if capacidad_max and t > capacidad_max + 1e-9:
                problemas.append(_problema(hojas["demanda"], dia, nodo,
                                           f"Pedido de {formato_num(t, 2)} t mayor que la capacidad del vehículo más grande "
                                           f"({formato_num(capacidad_max, 1)} t)."))


def preparar_datos(fuente, nombre_archivo: str = "", parametros: dict | None = None,
                   base: DatosRuteo | None = None) -> tuple[DatosRuteo | None, list]:
    """
    Lee y valida un libro completo o un libro con solo la hoja Demanda.

    - Libro completo (4 hojas): se usa todo lo que trae.
    - Solo Demanda: se combina con clientes, matriz y flota del archivo base.

    Devuelve (datos, problemas). Si hay algún problema de nivel "error", datos es None.
    """
    parametros = parametros or CONFIG
    hojas_cfg = parametros["hojas"]
    libro = leer_libro(fuente, parametros)
    problemas: list = []
    claves = {k: normalizar(v) for k, v in hojas_cfg.items()}
    presentes = {k for k, clave in claves.items() if clave in libro}
    if "demanda" not in presentes:
        problemas.append(_problema(hojas_cfg["demanda"], None, None, "Falta la hoja «Demanda»."))
        return None, problemas
    solo_demanda = presentes == {"demanda"}
    if not solo_demanda and presentes != set(claves):
        for k in sorted(set(claves) - presentes):
            problemas.append(_problema(hojas_cfg[k], None, None,
                                       f"Falta la hoja «{hojas_cfg[k]}». Cargue el libro completo o solo la hoja Demanda."))
        return None, problemas

    if solo_demanda:
        base = base or cargar_base(parametros)
        clientes, matriz, flota = base.clientes, base.matriz, base.flota
    else:
        nombre, df = libro[claves["clientes"]]
        clientes = leer_clientes(df, nombre, problemas)
        nombre, df = libro[claves["matriz"]]
        matriz = leer_matriz(df, nombre, problemas)
        nombre, df = libro[claves["vehiculos"]]
        flota = leer_vehiculos(df, nombre, problemas)
    nombre, df = libro[claves["demanda"]]
    dias, demanda = leer_demanda(df, nombre, problemas)
    _validar_nodos_listados(df, clientes, matriz, nombre, problemas)
    if any(p["nivel"] == "error" for p in problemas) or matriz is None:
        return None, problemas
    datos = DatosRuteo(clientes=clientes, matriz=matriz, flota=flota, dias=dias, demanda=demanda,
                       archivo=nombre_archivo, usa_datos_base=solo_demanda)
    validar_cruces(datos, problemas, parametros)
    for dia in dias:
        if demanda.get(dia) is None:
            problemas.append(_problema(hojas_cfg["demanda"], dia, None, "Columna vacía: día sin datos, no se calcula.", "aviso"))
    if solo_demanda:
        problemas.append(_problema(hojas_cfg["demanda"], None, None,
                                   "El libro solo trae la hoja Demanda: se usaron los clientes, la matriz y la flota del archivo base.",
                                   "aviso"))
    datos.advertencias = [p for p in problemas if p["nivel"] != "error"]
    if any(p["nivel"] == "error" for p in problemas):
        return None, problemas
    return datos, problemas


def _validar_nodos_listados(df, clientes, matriz, nombre_hoja, problemas):
    """Todo nodo listado en Demanda debe existir en Clientes y en la matriz, aunque no tenga pedido."""
    if not clientes or matriz is None:
        return
    tabla = _tabla_con_encabezado(df)
    col_nodo = _buscar_columna(tabla.columns, ["nodo"])
    if col_nodo is None:
        return
    nodos_clientes = {c["nodo"] for c in clientes}
    ya = {(p["hoja"], p["nodo"]) for p in problemas}
    for valor in tabla[col_nodo]:
        nodo = nodo_desde_rotulo(valor)
        if nodo is None or (nombre_hoja, str(nodo)) in ya:
            continue
        if nodo not in nodos_clientes:
            problemas.append(_problema(nombre_hoja, "Nodo", nodo, "El nodo no existe en la hoja Clientes."))
        elif nodo not in matriz.nodos:
            problemas.append(_problema(nombre_hoja, "Nodo", nodo, "El nodo no existe en la matriz de distancias."))


_CACHE_BASE: dict = {}


def cargar_base(parametros: dict | None = None) -> DatosRuteo:
    """Lee (una sola vez) el archivo base configurado en CONFIG['archivo_base']."""
    parametros = parametros or CONFIG
    ruta = parametros["archivo_base"]
    if ruta not in _CACHE_BASE:
        datos, problemas = preparar_datos(ruta, Path(ruta).name, parametros)
        if datos is None:
            detalle = "; ".join(f"{p['hoja']} {p['columna']} {p['nodo']}: {p['problema']}" for p in problemas if p["nivel"] == "error")
            raise ErrorArchivo(f"El archivo base tiene errores: {detalle}")
        _CACHE_BASE[ruta] = datos
    return _CACHE_BASE[ruta]


def resumen_previo(datos: DatosRuteo) -> dict:
    """Resumen antes de resolver: días con datos, clientes con pedido, toneladas y capacidad."""
    capacidad = sum(t.cantidad * t.capacidad_t for t in datos.flota)
    dias = []
    for dia in datos.dias:
        d = datos.demanda.get(dia)
        if d is None:
            dias.append({"dia": dia, "con_datos": False})
            continue
        total = sum(d.values())
        dias.append({"dia": dia, "con_datos": True, "clientes": len(d), "toneladas": round(total, 3),
                     "excede_capacidad": total > capacidad + 1e-9, "uso_pct": round(100 * total / capacidad, 1) if capacidad else None})
    return {
        "capacidad_flota_t": capacidad,
        "vehiculos_disponibles": sum(t.cantidad for t in datos.flota),
        "flota": [{"codigo": t.codigo, "tipo": t.tipo, "cantidad": t.cantidad, "capacidad_t": t.capacidad_t,
                   "costo_km": t.costo_km} for t in datos.flota],
        "dias": dias,
    }


# =============================================================================
# 5. PREPARACIÓN DEL MODELO (escalas enteras)
# =============================================================================


def expandir_flota(flota: list) -> list:
    """Una entrada por vehículo físico: [{id:'A1', codigo, tipo, capacidad_t, costo_km}]."""
    vehiculos = []
    for t in flota:
        for k in range(1, t.cantidad + 1):
            vehiculos.append({"id": f"{t.codigo}{k}", "codigo": t.codigo, "tipo": t.tipo,
                              "capacidad_t": t.capacidad_t, "costo_km": t.costo_km})
    return vehiculos


def preparar_instancia(demanda_dia: dict, matriz: Matriz, flota: list, parametros: dict) -> dict:
    """
    Construye la instancia entera para OR-Tools. Posición 0 = depot; 1..n = clientes con pedido.
    Distancias en metros, demandas en kg (redondeo hacia arriba), tiempos en segundos (hacia arriba)
    para que la solución entera nunca sea más optimista que la real.
    """
    depot = parametros["nodo_depot"]
    nodos = [depot] + sorted(n for n, t in demanda_dia.items() if t > 0)
    pos = [matriz.posicion(n) for n in nodos]
    km = matriz.km[np.ix_(pos, pos)]
    esc_d, esc_q = parametros["escala_distancia"], parametros["escala_demanda"]
    metros = np.rint(km * esc_d).astype(np.int64)
    seg_por_km = 3600.0 / parametros["velocidad_kmh"]
    viaje_s = np.ceil(km * seg_por_km - 1e-6).astype(np.int64)
    descarga_s = int(round(parametros["descarga_min"] * 60))
    servicio = np.array([0] + [descarga_s] * (len(nodos) - 1), dtype=np.int64)
    tiempo_s = viaje_s + servicio[:, None]          # transito i->j = descarga en i + viaje i->j
    np.fill_diagonal(tiempo_s, 0)
    vehiculos = expandir_flota(flota)
    demanda_kg = [0] + [int(math.ceil(demanda_dia[n] * esc_q - 1e-6)) for n in nodos[1:]]
    return {
        "nodos": nodos, "km": km, "metros": metros, "tiempo_s": tiempo_s, "viaje_s": viaje_s,
        "demanda_kg": demanda_kg, "vehiculos": vehiculos,
        "capacidad_kg": [int(math.floor(v["capacidad_t"] * esc_q + 1e-6)) for v in vehiculos],
        "costo_x100": [int(round(v["costo_km"] * 100)) for v in vehiculos],
        "limite_s": int(math.floor(parametros["jornada_max_min"] * 60 + 1e-6)),
        "descarga_s": descarga_s,
    }


# =============================================================================
# 6. SOLVER
# =============================================================================


def _crear_modelo(inst: dict, vehiculos_permitidos: dict | None = None, penalizacion: int | None = None):
    """
    Crea el modelo de OR-Tools: costo por vehículo, dimensión de carga y de tiempo.
    `penalizacion`: si se indica, un cliente puede quedar sin atender pagando ese costo
    (se usa solo en el respaldo para encontrar un punto de partida; nunca se entrega así).
    """
    n, m = len(inst["nodos"]), len(inst["vehiculos"])
    manager = pywrapcp.RoutingIndexManager(n, m, 0)
    routing = pywrapcp.RoutingModel(manager)
    # Costo de arco distinto por vehículo: metros × (COP/km × 100). Un callback por costo distinto.
    por_costo = {}
    for v, c in enumerate(inst["costo_x100"]):
        if c not in por_costo:
            por_costo[c] = routing.RegisterTransitMatrix((inst["metros"] * c).tolist())
        routing.SetArcCostEvaluatorOfVehicle(por_costo[c], v)
    # Capacidad (kg).
    cb_carga = routing.RegisterUnaryTransitVector(inst["demanda_kg"])
    routing.AddDimensionWithVehicleCapacity(cb_carga, 0, inst["capacidad_kg"], True, "Carga")
    # Tiempo (s): viaje + descarga, tope = jornada máxima (incluye regreso al depot).
    cb_tiempo = routing.RegisterTransitMatrix(inst["tiempo_s"].tolist())
    routing.AddDimension(cb_tiempo, 0, inst["limite_s"], True, "Tiempo")
    if vehiculos_permitidos:
        for pos, vs in vehiculos_permitidos.items():
            routing.VehicleVar(manager.NodeToIndex(pos)).SetValues([int(v) for v in vs])
    if penalizacion:
        for pos in range(1, n):
            routing.AddDisjunction([manager.NodeToIndex(pos)], int(penalizacion))
    return manager, routing


def _parametros_busqueda(estrategia: str, metaheuristica: str | None, segundos: float):
    """Parámetros de búsqueda de OR-Tools."""
    p = pywrapcp.DefaultRoutingSearchParameters()
    p.first_solution_strategy = getattr(routing_enums_pb2.FirstSolutionStrategy, estrategia)
    if metaheuristica:
        p.local_search_metaheuristic = getattr(routing_enums_pb2.LocalSearchMetaheuristic, metaheuristica)
    p.time_limit.FromMilliseconds(max(100, int(segundos * 1000)))
    return p


def _extraer_rutas(manager, routing, solucion, m: int) -> list:
    """Rutas como listas de posiciones (sin depot), una por vehículo."""
    rutas = []
    for v in range(m):
        idx, ruta = routing.Start(v), []
        idx = solucion.Value(routing.NextVar(idx))
        while not routing.IsEnd(idx):
            ruta.append(manager.IndexToNode(idx))
            idx = solucion.Value(routing.NextVar(idx))
        rutas.append(ruta)
    return rutas


def _costo_entero(inst: dict, rutas: list) -> int:
    """Costo entero (mismas unidades del modelo) de un conjunto de rutas."""
    total = 0
    for v, ruta in enumerate(rutas):
        sec = [0] + ruta + [0]
        total += sum(int(inst["metros"][a, b]) for a, b in zip(sec, sec[1:])) * inst["costo_x100"][v]
    return total


def _resolver_desde_rutas(inst: dict, rutas: list, segundos: float, parametros: dict):
    """Mejora un conjunto de rutas factibles con la metaheurística durante `segundos`."""
    manager, routing = _crear_modelo(inst)
    p = _parametros_busqueda("PATH_CHEAPEST_ARC", parametros["metaheuristica"], segundos)
    routing.CloseModelWithParameters(p)
    inicial = routing.ReadAssignmentFromRoutes([[manager.NodeToIndex(x) for x in r] for r in rutas], True)
    if inicial is None:
        return rutas
    sol = routing.SolveFromAssignmentWithParameters(inicial, p)
    return _extraer_rutas(manager, routing, sol, len(inst["vehiculos"])) if sol else rutas


def fase_soluciones_iniciales(inst: dict, parametros: dict, segundos_total: float) -> list:
    """
    Prueba varias estrategias de solución inicial (con búsqueda local simple).
    Devuelve [(costo, estrategia, rutas)] de las que encontraron solución factible.
    """
    estrategias = parametros["estrategias_iniciales"]
    por_estrategia = max(0.5, segundos_total / max(1, len(estrategias)))
    encontradas = []
    for estrategia in estrategias:
        manager, routing = _crear_modelo(inst)
        sol = routing.SolveWithParameters(_parametros_busqueda(estrategia, None, por_estrategia))
        if sol:
            rutas = _extraer_rutas(manager, routing, sol, len(inst["vehiculos"]))
            encontradas.append((_costo_entero(inst, rutas), estrategia, rutas))
    encontradas.sort(key=lambda x: x[0])
    return encontradas


def cota_tiempo_minimo(inst: dict) -> tuple[list, int]:
    """
    Cotas inferiores de tiempo (s): por cliente, descarga + llegada más corta posible;
    por vehículo usado, el regreso más corto al depot. Válidas para cualquier ruta.
    """
    n = len(inst["nodos"])
    viaje = inst["viaje_s"]
    por_cliente = []
    for c in range(1, n):
        llegadas = [int(viaje[j, c]) for j in range(n) if j != c]
        por_cliente.append(inst["descarga_s"] + min(llegadas))
    regreso_min = min(int(viaje[c, 0]) for c in range(1, n)) if n > 1 else 0
    return por_cliente, regreso_min


def asignar_por_empaque(inst: dict, con_tiempo: bool, segundos: float, cortes: list | None = None,
                        semilla: int = 0, compacto: bool = False) -> tuple[str, list | None]:
    """
    Respaldo tipo bin packing con CP-SAT: asigna clientes a vehículos sin dividir pedidos.
    Si `con_tiempo`, agrega una cota inferior de jornada por vehículo.
    `cortes`: conjuntos de clientes que no caben juntos en una jornada (no van en el mismo vehículo).
    Devuelve (estado, asignacion) con estado 'factible' | 'infactible' | 'desconocido'.
    """
    n, m = len(inst["nodos"]) - 1, len(inst["vehiculos"])
    modelo = cp_model.CpModel()
    x = {(c, v): modelo.NewBoolVar(f"x_{c}_{v}") for c in range(n) for v in range(m)}
    for c in range(n):
        modelo.AddExactlyOne(x[c, v] for v in range(m))
    for v in range(m):
        modelo.Add(sum(inst["demanda_kg"][c + 1] * x[c, v] for c in range(n)) <= inst["capacidad_kg"][v])
    if con_tiempo:
        por_cliente, regreso_min = cota_tiempo_minimo(inst)
        for v in range(m):
            usado = modelo.NewBoolVar(f"u_{v}")
            for c in range(n):
                modelo.AddImplication(x[c, v], usado)
            modelo.Add(sum(por_cliente[c] * x[c, v] for c in range(n)) + regreso_min * usado <= inst["limite_s"])
    if compacto:
        # Objetivo: agrupar clientes cercanos en el mismo vehículo (pares en el mismo vehículo × distancia).
        terminos = []
        for v in range(m):
            for c in range(n):
                for d in range(c + 1, n):
                    z = modelo.NewBoolVar("")
                    modelo.Add(z >= x[c, v] + x[d, v] - 1)
                    terminos.append(z * int(inst["metros"][c + 1, d + 1] + inst["metros"][d + 1, c + 1]))
        modelo.Minimize(sum(terminos))
    for conjunto in cortes or []:
        for v in range(m):
            modelo.Add(sum(x[c, v] for c in conjunto) <= len(conjunto) - 1)
    # Romper simetría entre vehículos idénticos: el de menor índice carga al menos lo mismo.
    for v in range(m - 1):
        a, b = inst["vehiculos"][v], inst["vehiculos"][v + 1]
        if a["codigo"] == b["codigo"]:
            modelo.Add(sum(inst["demanda_kg"][c + 1] * x[c, v] for c in range(n)) >=
                       sum(inst["demanda_kg"][c + 1] * x[c, v + 1] for c in range(n)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = segundos
    solver.parameters.num_workers = 1
    solver.parameters.random_seed = semilla
    estado = solver.Solve(modelo)
    if estado in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return "factible", [next(v for v in range(m) if solver.Value(x[c, v])) for c in range(n)]
    if estado == cp_model.INFEASIBLE:
        return "infactible", None
    return "desconocido", None


def _subinstancia(inst: dict, posiciones: list, v: int) -> dict:
    """Instancia reducida: depot + `posiciones`, con un único vehículo (el v-ésimo)."""
    idx = [0] + list(posiciones)
    sub = {k: inst[k][np.ix_(idx, idx)] for k in ("km", "metros", "tiempo_s", "viaje_s")}
    sub.update({"nodos": [inst["nodos"][i] for i in idx], "demanda_kg": [inst["demanda_kg"][i] for i in idx],
                "vehiculos": [inst["vehiculos"][v]], "capacidad_kg": [inst["capacidad_kg"][v]],
                "costo_x100": [inst["costo_x100"][v]], "limite_s": inst["limite_s"], "descarga_s": inst["descarga_s"]})
    return sub


def secuenciar_vehiculo(inst: dict, posiciones: list, v: int, segundos: float) -> list | None:
    """Ordena las paradas de un vehículo (TSP asimétrico con jornada). None si no cabe en la jornada."""
    sub = _subinstancia(inst, posiciones, v)
    manager, routing = _crear_modelo(sub)
    sol = routing.SolveWithParameters(_parametros_busqueda("PATH_CHEAPEST_ARC", "GUIDED_LOCAL_SEARCH", segundos))
    if not sol:
        return None
    orden = _extraer_rutas(manager, routing, sol, 1)[0]
    return [posiciones[p - 1] for p in orden]


def respaldo_penalizado(inst: dict, segundos: float, parametros: dict) -> list | None:
    """
    Respaldo 1: permite omitir clientes con una penalización muy alta. Así siempre existe un
    punto de partida y la metaheurística reduce las omisiones hasta cero. Solo se acepta si
    todos los clientes quedan atendidos.
    """
    penalizacion = int(inst["metros"].max()) * max(inst["costo_x100"]) * len(inst["nodos"]) * 10 + 1
    manager, routing = _crear_modelo(inst, penalizacion=penalizacion)
    sol = routing.SolveWithParameters(_parametros_busqueda("PARALLEL_CHEAPEST_INSERTION", parametros["metaheuristica"], segundos))
    if not sol:
        return None
    rutas = _extraer_rutas(manager, routing, sol, len(inst["vehiculos"]))
    return rutas if sum(len(r) for r in rutas) == len(inst["nodos"]) - 1 else None


def respaldo_empaque(inst: dict, segundos: float, parametros: dict) -> list | None:
    """
    Respaldo 2: asignación previa tipo bin packing (CP-SAT, capacidad + cota de jornada) y
    secuenciación de cada vehículo. Si un grupo no cabe en la jornada, se agrega un corte que
    impide repetirlo y se vuelve a asignar, hasta agotar el tiempo.
    """
    fin = time.perf_counter() + segundos
    cortes, intento = [], 0
    while time.perf_counter() < fin:
        restante = fin - time.perf_counter()
        estado, asignacion = asignar_por_empaque(inst, True, max(0.5, min(3.0, restante / 2)), cortes,
                                                 semilla=intento, compacto=True)
        if estado != "factible":
            return None
        rutas, ok = [], True
        for v in range(len(inst["vehiculos"])):
            posiciones = [c + 1 for c, a in enumerate(asignacion) if a == v]
            if not posiciones:
                rutas.append([])
                continue
            ruta = secuenciar_vehiculo(inst, posiciones, v, 0.5)
            if ruta is None:
                cortes.append({p - 1 for p in posiciones})
                ok = False
            rutas.append(ruta or [])
        if ok:
            return rutas
        intento += 1
    return None


def resolver_ruteo(demanda_dia: dict, matriz: Matriz, flota: list, parametros: dict | None = None) -> dict:
    """
    Resuelve el HFVRP de un día.

    Parámetros
    ----------
    demanda_dia : {nodo: toneladas}. Solo se visitan los nodos con demanda > 0.
    matriz      : Matriz de distancias viales (asimétrica, se usa tal cual).
    flota       : lista de TipoVehiculo.
    parametros  : diccionario de configuración (CONFIG por defecto).

    Devuelve
    --------
    dict con ``estado`` = 'resuelto' (y ``rutas``) o 'infactible' (y ``diagnostico``).
    """
    parametros = parametros or CONFIG
    inicio = time.perf_counter()
    demanda_dia = {n: float(t) for n, t in demanda_dia.items() if t and t > 0}
    vehiculos = expandir_flota(flota)
    if not demanda_dia:
        return {"estado": "resuelto", "rutas": [], "vehiculos": vehiculos,
                "calculo": {"estrategia": "sin clientes", "uso_respaldo": False, "segundos": 0.0}}

    diagnostico = diagnostico_previo(demanda_dia, matriz, flota, parametros)
    if diagnostico:
        return {"estado": "infactible", "diagnostico": diagnostico, "vehiculos": vehiculos,
                "calculo": {"segundos": round(time.perf_counter() - inicio, 2)}}

    inst = preparar_instancia(demanda_dia, matriz, flota, parametros)
    limite = parametros["tiempo_limite_s"]
    candidatos = fase_soluciones_iniciales(inst, parametros, limite * parametros["fraccion_fase_inicial"])
    uso_respaldo = False
    if candidatos:
        _, estrategia, rutas = candidatos[0]
    else:
        # Respaldos antes de declarar el día infactible.
        uso_respaldo = True
        estrategia = "Respaldo: modelo con penalización por cliente omitido"
        rutas = respaldo_penalizado(inst, limite * 0.35, parametros)
        if rutas is None:
            estrategia = "Respaldo: asignación previa tipo bin packing (CP-SAT)"
            rutas = respaldo_empaque(inst, parametros["tiempo_empaque_s"], parametros)
        if rutas is None:
            diagnostico = diagnostico_profundo(inst, demanda_dia, flota, parametros)
            return {"estado": "infactible", "diagnostico": diagnostico, "vehiculos": vehiculos,
                    "calculo": {"segundos": round(time.perf_counter() - inicio, 2), "uso_respaldo": True}}
    restante = max(1.0, limite - (time.perf_counter() - inicio))
    rutas = _resolver_desde_rutas(inst, rutas, restante, parametros)
    solucion = construir_solucion(inst, rutas, matriz, parametros)
    solucion["calculo"] = {"estrategia": estrategia, "uso_respaldo": uso_respaldo,
                           "estrategias_factibles": [c[1] for c in candidatos],
                           "segundos": round(time.perf_counter() - inicio, 2)}
    return solucion


def construir_solucion(inst: dict, rutas: list, matriz: Matriz, parametros: dict) -> dict:
    """Convierte rutas (posiciones) a la solución detallada, recalculada con km reales."""
    salida = []
    for v, ruta in enumerate(rutas):
        if not ruta:
            continue
        veh = inst["vehiculos"][v]
        salida.append({"vehiculo": veh["id"], "codigo": veh["codigo"], "tipo": veh["tipo"],
                       "capacidad_t": veh["capacidad_t"], "costo_km": veh["costo_km"],
                       "secuencia": [inst["nodos"][p] for p in ruta]})
    return {"estado": "resuelto", "rutas": salida, "vehiculos": inst["vehiculos"]}


# =============================================================================
# 7. DIAGNÓSTICO DE INFACTIBILIDAD
# =============================================================================


def diagnostico_previo(demanda_dia: dict, matriz: Matriz, flota: list, parametros: dict) -> dict | None:
    """Pruebas rápidas: demanda total vs. capacidad y clientes inalcanzables dentro de la jornada."""
    capacidad = sum(t.cantidad * t.capacidad_t for t in flota)
    total = sum(demanda_dia.values())
    if total > capacidad + 1e-9:
        return {
            "causa": "capacidad_total",
            "titulo": "Demanda mayor que la capacidad de la flota",
            "mensaje": (f"La demanda del día es {formato_num(total, 1)} t y la flota completa carga "
                        f"{formato_num(capacidad, 1)} t: faltan {formato_num(total - capacidad, 1)} t "
                        f"({formato_num(100 * (total - capacidad) / capacidad, 1)} %). "
                        "Se necesita más flota o reprogramar pedidos."),
            "cifras": {"demanda_t": total, "capacidad_t": capacidad, "faltante_t": total - capacidad},
        }
    depot, vel, desc = parametros["nodo_depot"], parametros["velocidad_kmh"], parametros["descarga_min"]
    jornada = parametros["jornada_max_min"]
    lejanos = []
    for nodo in demanda_dia:
        minutos = (matriz.distancia(depot, nodo) + matriz.distancia(nodo, depot)) / vel * 60 + desc
        if minutos > jornada + 1e-9:
            lejanos.append((nodo, minutos))
    if lejanos:
        detalle = ", ".join(f"nodo {n} ({formato_num(mi, 0)} min)" for n, mi in lejanos)
        return {
            "causa": "jornada",
            "titulo": "Jornada insuficiente",
            "mensaje": (f"Solo ir, descargar y volver toma más de la jornada de {formato_num(jornada, 0)} min en: {detalle}."),
            "cifras": {"clientes": [n for n, _ in lejanos], "jornada_min": jornada},
        }
    return None


def diagnostico_profundo(inst: dict, demanda_dia: dict, flota: list, parametros: dict) -> dict:
    """
    Diagnóstico cuando el ruteo no encontró solución:
    1) CP-SAT solo con capacidad -> los pedidos no caben sin dividirlos.
    2) CP-SAT con capacidad + cota de jornada -> la jornada no alcanza.
    3) Ninguna prueba concluyente -> no se halló solución en el tiempo límite.
    """
    total = sum(demanda_dia.values())
    capacidad = sum(v["capacidad_t"] for v in inst["vehiculos"])
    estado_cap, _ = asignar_por_empaque(inst, False, parametros["tiempo_empaque_s"])
    if estado_cap == "infactible":
        pedidos = sorted(demanda_dia.values(), reverse=True)
        caps = ", ".join(f"{v['id']} {formato_num(v['capacidad_t'], 1)} t" for v in inst["vehiculos"])
        return {
            "causa": "empaque",
            "titulo": "Los pedidos no caben sin dividirlos",
            "mensaje": (f"La demanda ({formato_num(total, 1)} t) cabe en la capacidad total ({formato_num(capacidad, 1)} t), "
                        f"pero no existe forma de repartir los {len(pedidos)} pedidos completos entre los vehículos "
                        f"({caps}). Holgura total: {formato_num(capacidad - total, 2)} t; pedido más pequeño: "
                        f"{formato_num(pedidos[-1], 2)} t."),
            "cifras": {"demanda_t": total, "capacidad_t": capacidad, "holgura_t": capacidad - total},
        }
    por_cliente, regreso_min = cota_tiempo_minimo(inst)
    minimo_h = (sum(por_cliente) + regreso_min) / 3600
    disponible_h = len(inst["vehiculos"]) * inst["limite_s"] / 3600
    estado_t, _ = asignar_por_empaque(inst, True, parametros["tiempo_empaque_s"])
    if estado_t == "infactible":
        return {
            "causa": "jornada",
            "titulo": "Jornada insuficiente",
            "mensaje": (f"Los pedidos caben por peso, pero no en el tiempo: atender a los {len(por_cliente)} clientes exige "
                        f"como mínimo {formato_num(minimo_h, 1)} h de trabajo (descargas de {formato_num(parametros['descarga_min'], 0)} min "
                        f"más los viajes más cortos posibles) y no es posible repartirlo en {len(inst['vehiculos'])} vehículo(s) "
                        f"con jornadas de {formato_num(inst['limite_s'] / 3600, 1)} h ({formato_num(disponible_h, 1)} h en total) "
                        "respetando a la vez la capacidad."),
            "cifras": {"horas_minimas": minimo_h, "horas_disponibles": disponible_h},
        }
    return {
        "causa": "sin_solucion",
        "titulo": "Sin solución en el tiempo límite",
        "mensaje": (f"No se encontró un plan que cumpla capacidad ({formato_num(total, 1)} de {formato_num(capacidad, 1)} t) "
                    f"y jornada (mínimo estimado {formato_num(minimo_h, 1)} de {formato_num(disponible_h, 1)} h) en "
                    f"{formato_num(parametros['tiempo_limite_s'], 0)} s. No se demostró que sea imposible: "
                    "pruebe con más tiempo de cálculo."),
        "cifras": {"demanda_t": total, "capacidad_t": capacidad, "horas_minimas": minimo_h, "horas_disponibles": disponible_h},
    }


# =============================================================================
# 8. EVALUACIÓN DE RUTAS, VERIFICACIÓN E INDICADORES
# =============================================================================


def evaluar_ruta(secuencia: list, demanda_dia: dict, matriz: Matriz, parametros: dict) -> dict:
    """
    Recalcula una ruta desde la matriz original, respetando el sentido de cada tramo.
    Devuelve km, minutos, carga y el detalle de paradas (ETA y carga restante).
    """
    depot = parametros["nodo_depot"]
    vel, desc = parametros["velocidad_kmh"], parametros["descarga_min"]
    carga = sum(demanda_dia.get(n, 0.0) for n in secuencia)
    reloj, km_total, actual, restante, paradas = 0.0, 0.0, depot, carga, []
    for orden, nodo in enumerate(secuencia, start=1):
        tramo = matriz.distancia(actual, nodo)
        km_total += tramo
        reloj += tramo / vel * 60
        restante -= demanda_dia.get(nodo, 0.0)
        paradas.append({"orden": orden, "nodo": nodo, "km_tramo": tramo, "llegada_min": reloj,
                        "entrega_t": demanda_dia.get(nodo, 0.0), "carga_restante_t": max(0.0, restante)})
        reloj += desc
        actual = nodo
    regreso = matriz.distancia(actual, depot)
    km_total += regreso
    reloj += regreso / vel * 60
    return {"km": km_total, "minutos": reloj, "carga_t": carga, "paradas": paradas, "km_regreso": regreso}


def verificar_solucion(solucion: dict, demanda_dia: dict, matriz: Matriz, flota: list,
                       parametros: dict | None = None) -> list:
    """
    Tabla de cumplimiento de restricciones, recalculada desde la matriz original.
    Devuelve [{criterio, cumple, detalle}].
    """
    parametros = parametros or CONFIG
    depot = parametros["nodo_depot"]
    rutas = solucion.get("rutas", [])
    con_demanda = {n for n, t in demanda_dia.items() if t > 0}
    visitas = [n for r in rutas for n in r["secuencia"]]
    filas = []

    repetidos = sorted({n for n in visitas if visitas.count(n) > 1})
    faltantes = sorted(con_demanda - set(visitas))
    sobrantes = sorted(set(visitas) - con_demanda)
    filas.append({"criterio": "Cada cliente con demanda se visita exactamente una vez; los de demanda cero no",
                  "cumple": not (repetidos or faltantes or sobrantes),
                  "detalle": (f"{len(set(visitas) & con_demanda)} de {len(con_demanda)} atendidos"
                              + (f"; repetidos {repetidos}" if repetidos else "")
                              + (f"; faltan {faltantes}" if faltantes else "")
                              + (f"; sin demanda visitados {sobrantes}" if sobrantes else ""))})

    secuencias_ok = all(depot not in r["secuencia"] and r["secuencia"] for r in rutas)
    filas.append({"criterio": "Toda ruta inicia y termina en el depot (nodo 0)",
                  "cumple": secuencias_ok,
                  "detalle": f"{len(rutas)} rutas: 0 → … → 0" if secuencias_ok else "Hay rutas vacías o que pasan por el depot"})

    cap_ok, peor_cap = True, 0.0
    dur_ok, peor_dur = True, 0.0
    costo = 0.0
    for r in rutas:
        ev = evaluar_ruta(r["secuencia"], demanda_dia, matriz, parametros)
        peor_cap = max(peor_cap, ev["carga_t"] / r["capacidad_t"])
        cap_ok &= ev["carga_t"] <= r["capacidad_t"] + 1e-9
        peor_dur = max(peor_dur, ev["minutos"])
        dur_ok &= ev["minutos"] <= parametros["jornada_max_min"] + 1e-6
        costo += ev["km"] * r["costo_km"]
    filas.append({"criterio": "Carga ≤ capacidad en cada ruta", "cumple": cap_ok,
                  "detalle": f"máxima utilización {formato_num(100 * peor_cap, 1)} %"})
    filas.append({"criterio": f"Duración ≤ {formato_num(parametros['jornada_max_min'], 0)} min en cada ruta (recalculada)",
                  "cumple": dur_ok, "detalle": f"ruta más larga {formato_num(peor_dur, 1)} min"})

    disponibles = {t.codigo: t.cantidad for t in flota}
    usados: dict = {}
    for r in rutas:
        usados[r["codigo"]] = usados.get(r["codigo"], 0) + 1
    ids = [r["vehiculo"] for r in rutas]
    flota_ok = all(usados.get(c, 0) <= disponibles.get(c, 0) for c in usados) and len(ids) == len(set(ids))
    filas.append({"criterio": "Vehículos usados ≤ disponibles por tipo", "cumple": flota_ok,
                  "detalle": " · ".join(f"{c}: {usados.get(c, 0)}/{disponibles[c]}" for c in disponibles)})

    reportado = solucion.get("indicadores", {}).get("costo_total")
    costo_ok = reportado is not None and abs(reportado - costo) < 0.5
    filas.append({"criterio": "Costo del tablero = costo recalculado desde la matriz (sentido de cada tramo)",
                  "cumple": costo_ok,
                  "detalle": f"tablero {formato_cop(reportado or 0)} · recalculado {formato_cop(costo)}"})
    return filas


def calcular_indicadores(solucion: dict, demanda_dia: dict, matriz: Matriz, flota: list, parametros: dict,
                         clientes: list | None = None) -> dict:
    """
    Agrega a la solución el detalle por ruta y los 5 indicadores del tablero.
    Modifica y devuelve `solucion`.
    """
    inicio = parametros["inicio_jornada"]
    jornada = parametros["jornada_max_min"]
    alerta, sub = parametros["umbral_alerta_pct"], parametros["umbral_subutilizado_pct"]
    info = {c["nodo"]: c for c in (clientes or [])}
    rutas = []
    for r in solucion.get("rutas", []):
        ev = evaluar_ruta(r["secuencia"], demanda_dia, matriz, parametros)
        pct_cap = 100 * ev["carga_t"] / r["capacidad_t"]
        pct_jor = 100 * ev["minutos"] / jornada
        paradas = []
        for p in ev["paradas"]:
            c = info.get(p["nodo"], {})
            paradas.append({**p, "cliente": c.get("nombre", f"Nodo {p['nodo']}"), "direccion": c.get("direccion", ""),
                            "eta": minutos_a_hora(p["llegada_min"], inicio),
                            "salida": minutos_a_hora(p["llegada_min"] + parametros["descarga_min"], inicio)})
        rutas.append({
            **r, "paradas": paradas, "n_paradas": len(paradas),
            "carga_t": ev["carga_t"], "pct_capacidad": pct_cap,
            "km": ev["km"], "km_regreso": ev["km_regreso"], "duracion_min": ev["minutos"], "pct_jornada": pct_jor,
            "hora_regreso": minutos_a_hora(ev["minutos"], inicio), "holgura_min": jornada - ev["minutos"],
            "costo": ev["km"] * r["costo_km"],
            # Umbrales sobre el porcentaje redondeado, igual a como se muestra en el tablero.
            "alerta_capacidad": round(pct_cap) >= alerta, "alerta_jornada": round(pct_jor) >= alerta,
            "subutilizado": round(pct_cap) < sub,
        })
    solucion["rutas"] = rutas
    vehiculos = solucion.get("vehiculos") or expandir_flota(flota)
    usados = {r["vehiculo"] for r in rutas}
    costo = sum(r["costo"] for r in rutas)
    km = sum(r["km"] for r in rutas)
    toneladas = sum(r["carga_t"] for r in rutas)
    cap_usada = sum(r["capacidad_t"] for r in rutas)
    larga = max(rutas, key=lambda r: r["duracion_min"]) if rutas else None
    por_tipo = []
    for t in flota:
        n_usados = sum(1 for r in rutas if r["codigo"] == t.codigo)
        por_tipo.append({"codigo": t.codigo, "usados": n_usados, "disponibles": t.cantidad})
    solucion["sin_despachar"] = [v["id"] for v in vehiculos if v["id"] not in usados]
    solucion["indicadores"] = {
        "costo_total": costo,
        "costo_por_t": costo / toneladas if toneladas else 0.0,
        "km_total": km,
        "costo_por_km": costo / km if km else 0.0,
        "vehiculos_usados": len(rutas),
        "vehiculos_disponibles": len(vehiculos),
        "por_tipo": por_tipo,
        "toneladas": toneladas,
        "capacidad_usada_t": cap_usada,
        "utilizacion_pct": 100 * toneladas / cap_usada if cap_usada else 0.0,
        "regreso_max": larga["hora_regreso"] if larga else inicio,
        "vehiculo_regreso_max": larga["vehiculo"] if larga else "",
        "holgura_min": (jornada - larga["duracion_min"]) if larga else jornada,
        "hora_limite": minutos_a_hora(jornada, inicio),
        "clientes_atendidos": sum(r["n_paradas"] for r in rutas),
        "clientes_con_demanda": sum(1 for t in demanda_dia.values() if t > 0),
    }
    return solucion


# =============================================================================
# 9. RESOLUCIÓN POR DÍA Y POR ARCHIVO
# =============================================================================


def resolver_dia(datos: DatosRuteo, dia: str, parametros: dict | None = None) -> dict:
    """
    Resuelve, calcula indicadores y verifica un día. Devuelve un dict serializable a JSON.
    Estados: 'sin_datos', 'resuelto', 'infactible' o 'error'.
    """
    parametros = parametros or CONFIG
    demanda_dia = datos.demanda.get(dia)
    if demanda_dia is None:
        return {"dia": dia, "estado": "sin_datos", "mensaje": "La columna del día está vacía."}
    base = {"dia": dia, "demanda": {str(n): t for n, t in demanda_dia.items()},
            "demanda_total_t": sum(demanda_dia.values()), "clientes_con_pedido": len(demanda_dia)}
    try:
        sol = resolver_ruteo(demanda_dia, datos.matriz, datos.flota, parametros)
    except Exception as e:  # nunca mostrar trazas al usuario
        return {**base, "estado": "error", "mensaje": f"Error inesperado al calcular {dia}: {type(e).__name__}."}
    if sol["estado"] == "infactible":
        return _a_python({**base, "estado": "infactible", "diagnostico": sol["diagnostico"], "calculo": sol["calculo"],
                          "sin_despachar": [v["id"] for v in sol["vehiculos"]]})
    calcular_indicadores(sol, demanda_dia, datos.matriz, datos.flota, parametros, datos.clientes)
    verificacion = verificar_solucion(sol, demanda_dia, datos.matriz, datos.flota, parametros)
    resultado = {**base, "estado": "resuelto", "indicadores": sol["indicadores"], "rutas": sol["rutas"],
                 "sin_despachar": sol["sin_despachar"], "verificacion": verificacion,
                 "verificacion_ok": all(f["cumple"] for f in verificacion), "calculo": sol["calculo"]}
    if not resultado["verificacion_ok"]:
        resultado["estado"] = "error"
        resultado["mensaje"] = "La solución no superó la verificación de restricciones; no se muestra como válida."
    return _a_python(resultado)


def encabezado_resultados(datos: DatosRuteo, parametros: dict, problemas: list) -> dict:
    """Parte común del resultado: depot, clientes, flota, parámetros, resumen previo y avisos."""
    depot = next(c for c in datos.clientes if c["nodo"] == parametros["nodo_depot"])
    return _a_python({
        "estado": "valido",
        "archivo": datos.archivo,
        "fecha_calculo": datetime.now().isoformat(timespec="seconds"),
        "usa_datos_base": datos.usa_datos_base,
        "depot": depot,
        "clientes": datos.clientes,
        "flota": resumen_previo(datos)["flota"],
        "vehiculos": [v["id"] for v in expandir_flota(datos.flota)],
        "parametros": {k: parametros[k] for k in ("velocidad_kmh", "descarga_min", "inicio_jornada", "jornada_max_min",
                                                   "tiempo_limite_s", "umbral_alerta_pct", "umbral_subutilizado_pct")},
        "resumen_previo": resumen_previo(datos),
        "avisos": [p for p in problemas if p["nivel"] != "error"],
        "dias": [{"dia": d, "estado": "pendiente" if datos.demanda.get(d) is not None else "sin_datos"} for d in datos.dias],
    })


def resumen_final(resultados: dict) -> str:
    """Mensaje breve al terminar: días resueltos, sin datos, infactibles."""
    cuenta = {}
    for d in resultados["dias"]:
        cuenta[d["estado"]] = cuenta.get(d["estado"], 0) + 1
    partes = [f"{cuenta.get('resuelto', 0)} día(s) resuelto(s)"]
    if cuenta.get("sin_datos"):
        partes.append(f"{cuenta['sin_datos']} sin datos")
    if cuenta.get("infactible"):
        partes.append(f"{cuenta['infactible']} infactible(s)")
    if cuenta.get("error"):
        partes.append(f"{cuenta['error']} con error")
    return " · ".join(partes)


def _numero_procesos(parametros: dict, n: int) -> int:
    """Procesos para resolver días en paralelo."""
    return max(1, min(n, parametros.get("procesos_paralelos") or os.cpu_count() or 1))


def resolver_archivo(ruta_o_bytes, nombre_archivo: str | None = None, parametros: dict | None = None,
                     paralelo: bool = True, progreso=None) -> dict:
    """
    Lee, valida y resuelve todos los días de un libro. Devuelve una estructura serializable a JSON.
    Si hay errores de validación: {'estado': 'invalido', 'errores': [...]} y no se ejecuta el solver.
    `progreso(dia, resultado_dia, hechos, total)`: función opcional llamada al terminar cada día.
    """
    parametros = parametros or CONFIG
    if nombre_archivo is None:
        nombre_archivo = Path(ruta_o_bytes).name if isinstance(ruta_o_bytes, (str, Path)) else "archivo.xlsx"
    try:
        datos, problemas = preparar_datos(ruta_o_bytes, nombre_archivo, parametros)
    except ErrorArchivo as e:
        return {"estado": "invalido", "archivo": nombre_archivo,
                "errores": [_problema("", None, None, str(e))]}
    if datos is None:
        return {"estado": "invalido", "archivo": nombre_archivo,
                "errores": [p for p in problemas if p["nivel"] == "error"]}
    resultados = encabezado_resultados(datos, parametros, problemas)
    dias = [d for d in datos.dias if datos.demanda.get(d) is not None]
    por_dia = {}

    def registrar(dia, resultado):
        por_dia[dia] = resultado
        if progreso:
            progreso(dia, resultado, len(por_dia), len(dias))

    if paralelo and len(dias) > 1:
        with ProcessPoolExecutor(max_workers=_numero_procesos(parametros, len(dias))) as ex:
            futuros = {ex.submit(resolver_dia, datos, d, parametros): d for d in dias}
            for futuro in as_completed(futuros):
                registrar(futuros[futuro], futuro.result())
    else:
        for d in dias:
            registrar(d, resolver_dia(datos, d, parametros))
    resultados["dias"] = [por_dia.get(d) or resolver_dia(datos, d, parametros) for d in datos.dias]
    resultados["mensaje_final"] = resumen_final(resultados)
    return resultados


def tabla_resumen(resultados: dict) -> pd.DataFrame:
    """Tabla por día: costo, km, vehículos, utilización y holgura (para notebook y reporte)."""
    filas = []
    for d in resultados["dias"]:
        fila = {"Día": d["dia"], "Estado": d["estado"]}
        if d["estado"] == "resuelto":
            i = d["indicadores"]
            fila.update({"Demanda (t)": round(d["demanda_total_t"], 2), "Costo (COP)": round(i["costo_total"]),
                         "COP por t": round(i["costo_por_t"]), "Km": round(i["km_total"], 1),
                         "Vehículos": f"{i['vehiculos_usados']}/{i['vehiculos_disponibles']}",
                         "Utilización (%)": round(i["utilizacion_pct"], 1), "Regreso más tardío": i["regreso_max"],
                         "Holgura (min)": int(math.floor(i["holgura_min"]))})
        elif d["estado"] == "infactible":
            fila.update({"Demanda (t)": round(d["demanda_total_t"], 2), "Diagnóstico": d["diagnostico"]["titulo"]})
        filas.append(fila)
    return pd.DataFrame(filas)


# =============================================================================
# 10. FORMATO COLOMBIANO Y EXPORTES
# =============================================================================


def formato_num(valor: float, decimales: int = 0) -> str:
    """Número con punto de miles y coma decimal: 1234.5 -> '1.234,5'."""
    texto = f"{valor:,.{decimales}f}"
    return texto.replace(",", "§").replace(".", ",").replace("§", ".")


def formato_cop(valor: float) -> str:
    """Pesos colombianos sin decimales: '$ 1.234.567'."""
    return f"$ {formato_num(round(valor), 0)}"


def exportar_rutas_excel(resultados: dict) -> bytes:
    """Genera rutas.xlsx: hoja Resumen + una hoja de paradas por día resuelto (+ diagnósticos)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    titulo = Font(bold=True, size=13)
    negrita = Font(bold=True, color="FFFFFF")
    relleno = PatternFill("solid", fgColor="1F4E79")
    ws.append([f"Plan de rutas — {resultados.get('depot', {}).get('nombre', '')}"])
    ws["A1"].font = titulo
    ws.append([f"Archivo: {resultados.get('archivo', '')} · Calculado: {resultados.get('fecha_calculo', '').replace('T', ' ')}"])
    ws.append([])
    encabezado = ["Día", "Estado", "Demanda (t)", "Costo (COP)", "COP por t", "Km", "COP por km", "Vehículos usados",
                  "Disponibles", "Utilización (%)", "Regreso más tardío", "Holgura (min)", "Clientes atendidos", "Diagnóstico"]
    ws.append(encabezado)
    for c in ws[4]:
        c.font, c.fill = negrita, relleno
    estados = {"resuelto": "Resuelto", "sin_datos": "Sin datos", "infactible": "Infactible", "error": "Error", "pendiente": "Pendiente"}
    for d in resultados.get("dias", []):
        if d["estado"] == "resuelto":
            i = d["indicadores"]
            ws.append([d["dia"], "Resuelto", round(d["demanda_total_t"], 2), round(i["costo_total"]), round(i["costo_por_t"]),
                       round(i["km_total"], 1), round(i["costo_por_km"]), i["vehiculos_usados"], i["vehiculos_disponibles"],
                       round(i["utilizacion_pct"], 1), i["regreso_max"], int(math.floor(i["holgura_min"])),
                       f"{i['clientes_atendidos']}/{i['clientes_con_demanda']}", ""])
        else:
            diag = d.get("diagnostico", {}).get("mensaje", d.get("mensaje", ""))
            ws.append([d["dia"], estados.get(d["estado"], d["estado"]), round(d.get("demanda_total_t", 0), 2) or None,
                       None, None, None, None, None, None, None, None, None, None, diag])
    for fila in ws.iter_rows(min_row=5):
        for col in (4, 5, 7):
            fila[col - 1].number_format = "#,##0"
    _ajustar_anchos(ws, get_column_letter)

    for d in resultados.get("dias", []):
        if d["estado"] != "resuelto":
            continue
        hoja = wb.create_sheet(_nombre_hoja(d["dia"]))
        hoja.append([f"{d['dia']} — paradas por vehículo"])
        hoja["A1"].font = titulo
        hoja.append([])
        cab = ["Vehículo", "Tipo", "Orden", "Nodo", "Cliente", "Dirección", "ETA", "Salida", "Entrega (t)",
               "Carga restante (t)", "Km tramo"]
        hoja.append(cab)
        for c in hoja[3]:
            c.font, c.fill = negrita, relleno
        for r in d["rutas"]:
            hoja.append([r["vehiculo"], r["tipo"], 0, 0, "Salida del depot", resultados.get("depot", {}).get("direccion", ""),
                         resultados.get("parametros", {}).get("inicio_jornada", ""), "", None, round(r["carga_t"], 3), None])
            for p in r["paradas"]:
                hoja.append([r["vehiculo"], r["tipo"], p["orden"], p["nodo"], p["cliente"], p["direccion"], p["eta"],
                             p["salida"], round(p["entrega_t"], 3), round(p["carga_restante_t"], 3), round(p["km_tramo"], 2)])
            hoja.append([r["vehiculo"], r["tipo"], len(r["paradas"]) + 1, 0, "Regreso al depot", "", r["hora_regreso"], "",
                         None, 0, round(r["km_regreso"], 2)])
            hoja.append([f"Total {r['vehiculo']}", "", "", "", f"{r['n_paradas']} paradas · {formato_num(r['km'], 1)} km · "
                         f"{formato_cop(r['costo'])} · carga {formato_num(r['carga_t'], 1)}/{formato_num(r['capacidad_t'], 1)} t"])
            hoja.cell(hoja.max_row, 1).font = Font(bold=True)
            hoja.append([])
        _ajustar_anchos(hoja, get_column_letter)
        hoja.freeze_panes = "A4"
    for h in wb.worksheets:
        for fila in h.iter_rows():
            for c in fila:
                c.alignment = Alignment(vertical="top")
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()


VENDOR_CDN = {
    "vendor/leaflet.css": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
    "vendor/leaflet.js": "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
    "vendor/chart.umd.min.js": "https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/chart.umd.js",
}


def clave_carto(directorio=None) -> str:
    """
    Clave de CARTO Basemaps para quitar la marca de agua del mapa base.
    Se lee de la variable de entorno CARTO_API_KEY o, si no existe, del archivo .env del proyecto
    (línea CARTO_API_KEY=...). Nunca se escribe en el código. Devuelve "" si no hay clave.
    """
    clave = os.environ.get("CARTO_API_KEY", "").strip()
    if clave:
        return clave
    archivo = Path(directorio or DIRECTORIO) / ".env"
    if archivo.exists():
        for linea in archivo.read_text(encoding="utf-8-sig").splitlines():
            nombre, _, valor = linea.partition("=")
            if nombre.strip() == "CARTO_API_KEY":
                return valor.strip().strip('"').strip("'")
    return ""


def url_mapa_base(estilo: str = "light_all", clave: str = "") -> str:
    """Plantilla de teselas raster de CARTO (light_all / dark_all), con la clave si existe."""
    url = f"https://{{s}}.basemaps.cartocdn.com/{estilo}/{{z}}/{{x}}/{{y}}{{r}}.png"
    return url + (f"?key={clave}" if clave else "")


def script_configuracion_mapa(clave: str = "") -> str:
    """Etiqueta <script> que entrega la clave del mapa base al tablero."""
    return f"<script>window.CLAVE_MAPA = {json.dumps(clave or '')};</script>"


def exportar_tablero_html(resultados: dict, directorio_static=None, clave_mapa: str | None = None) -> str:
    """
    Tablero en un solo archivo HTML: CSS y JavaScript en línea y los resultados incrustados.
    Si falta una librería local (Leaflet, Chart.js), se enlaza desde su CDN.
    Solo el mapa base y, en ese caso, las librerías requieren internet.
    `clave_mapa`: clave de CARTO (por defecto, la de clave_carto()). Queda visible en el HTML,
    como en cualquier mapa web que use CARTO.
    """
    clave_mapa = clave_carto() if clave_mapa is None else clave_mapa
    estaticos = Path(directorio_static or DIRECTORIO / "static")
    html = (estaticos / "index.html").read_text(encoding="utf-8")

    def estilo(m):
        ruta = estaticos / m.group(1)
        if not ruta.exists():
            return f'<link rel="stylesheet" href="{VENDOR_CDN.get(m.group(1), m.group(1))}">'
        return f"<style>\n{ruta.read_text(encoding='utf-8')}\n</style>"

    def guion(m):
        ruta = estaticos / m.group(1)
        if not ruta.exists():
            return f'<script src="{VENDOR_CDN.get(m.group(1), m.group(1))}"></script>'
        codigo = ruta.read_text(encoding="utf-8").replace("</script", "<\\/script")
        return f"<script>\n{codigo}\n</script>"

    html = re.sub(r'<link rel="stylesheet" href="/static/([^"]+)">', estilo, html)
    datos = json.dumps(_a_python(resultados), ensure_ascii=False).replace("</", "<\\/")
    html = html.replace("<!--DATOS_EXPORTADOS-->",
                        script_configuracion_mapa(clave_mapa) + f"<script>window.DATOS_EXPORTADOS = {datos};</script>")
    return re.sub(r'<script src="/static/([^"]+)"></script>', guion, html)


def _nombre_hoja(dia: str) -> str:
    """Nombre de hoja válido para Excel (máx. 31 caracteres, sin caracteres prohibidos)."""
    return re.sub(r"[\[\]\*\?/\\:]", "", str(dia))[:31] or "Día"


def _ajustar_anchos(hoja, get_column_letter):
    """Ancho de columnas según el contenido (acotado)."""
    anchos = {}
    for fila in hoja.iter_rows(min_row=3):
        for c in fila:
            if c.value is not None:
                anchos[c.column] = max(anchos.get(c.column, 0), len(str(c.value)))
    for col, ancho in anchos.items():
        hoja.column_dimensions[get_column_letter(col)].width = min(48, max(9, ancho + 2))


def generar_plantilla(parametros: dict | None = None, dias: list | None = None) -> bytes:
    """
    Plantilla de carga: libro con las 4 hojas del archivo base y la hoja Demanda con las
    columnas de días vacías, lista para llenar. Incluye una hoja de instrucciones.
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    parametros = parametros or CONFIG
    base = cargar_base(parametros)
    wb = load_workbook(parametros["archivo_base"])
    hoja = wb[next(n for n in wb.sheetnames if normalizar(n) == normalizar(parametros["hojas"]["demanda"]))]
    dias = dias or base.dias
    hoja.delete_rows(1, hoja.max_row)
    hoja.append(["Nodo", "Cliente", *dias])
    for c in hoja[1]:
        c.font = Font(bold=True)
    for c in base.clientes:
        if c["nodo"] != parametros["nodo_depot"]:
            hoja.append([c["nodo"], c["nombre"]])
    ins = wb.create_sheet("Instrucciones", 0)
    for linea in [
        "Cómo usar esta plantilla",
        "",
        "1. Escriba en la hoja Demanda las toneladas de cada cliente en la columna del día (use coma o punto decimal).",
        "2. Deje en blanco o en 0 los clientes que no reciben pedido ese día.",
        "3. Un día con la columna completamente vacía se omite y se informa como «Sin datos».",
        "4. Puede agregar, quitar o renombrar columnas de días: se leen todas las que estén a la derecha de «Cliente».",
        "5. Si cambia clientes, distancias o flota, edite también las hojas Clientes, Matriz_Distancias y Vehiculos.",
        "6. También puede cargar un libro que solo tenga la hoja Demanda: se usarán los datos base del aplicativo.",
        "7. Guarde como .xlsx y cárguelo con el botón «Cargar archivo de Excel».",
    ]:
        ins.append([linea])
    ins["A1"].font = Font(bold=True, size=13)
    ins.column_dimensions["A"].width = 110
    salida = io.BytesIO()
    wb.save(salida)
    return salida.getvalue()
