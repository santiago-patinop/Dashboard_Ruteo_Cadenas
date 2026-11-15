"""Construye ruteo_distribucion.ipynb (las celdas solo llaman funciones de ruteo.py)."""
import nbformat as nbf

nb = nbf.v4.new_notebook()
c = []
md = lambda t: c.append(nbf.v4.new_markdown_cell(t.strip()))
code = lambda t: c.append(nbf.v4.new_code_cell(t.strip()))

md("""
# Ruteo de distribución urbana — Valle de Aburrá

**Problema:** cada día una flota heterogénea sale del Centro de Distribución (nodo 0), entrega pedidos
en puntos de venta y regresa al mismo depot. Se busca el plan de **mínimo costo operativo**
(km × costo/km del vehículo asignado) que respete capacidad, jornada y flota disponible.
Es un **HFVRP** (*Heterogeneous Fleet Vehicle Routing Problem*) con límite de jornada.

Este notebook **no implementa lógica propia**: importa `ruteo.py`, el mismo módulo que usa la aplicación web.

| Paso | Contenido |
|---|---|
| 1 | Parámetros de operación |
| 2 | Lectura de datos |
| 3 | Validación |
| 4 | Modelo matemático y método |
| 5 | Un día paso a paso (Lunes) |
| 6 | Todos los días |
| 7 | Detalle de rutas |
| 8 | Verificación de restricciones |
| 9 | Días infactibles: diagnóstico |
| 10 | Cómo iniciar la aplicación |
""")

code("""
import json
from pathlib import Path

import pandas as pd

import ruteo

pd.set_option("display.max_columns", 30)
pd.set_option("display.width", 200)
ARCHIVO = Path(ruteo.CONFIG["archivo_base"])
print("Archivo:", ARCHIVO.name)
""")

md("""
## 1. Parámetros de operación

Todos los parámetros viven en un único bloque, `ruteo.CONFIG`, compartido con la aplicación.
Para cambiar uno basta `ruteo.parametros_por_defecto(limite_iteraciones=40000)`.
""")
code("""
pd.DataFrame(
    [(k, v) for k, v in ruteo.CONFIG.items() if k not in ("hojas", "archivo_base")],
    columns=["Parámetro", "Valor"],
)
""")

md("""
## 2. Lectura de datos

`preparar_datos` lee las cuatro hojas y las convierte a estructuras limpias:

- **Clientes:** nodo, nombre, dirección y coordenadas. Las coordenadas solo se usan para el mapa.
- **Matriz_Distancias:** el número de nodo se extrae del rótulo `"N - Nombre"`. Fila = origen y columna = destino.
  Se usa **tal como viene**, asimétrica.
- **Vehiculos:** capacidad y costo vienen como texto (`"10 toneladas"`, `"4.200 COP/km"`). El punto es separador de miles.
- **Demanda:** los días son las columnas a la derecha de `Cliente`, cualesquiera que sean sus nombres.
""")
code("""
datos, problemas = ruteo.preparar_datos(ARCHIVO, ARCHIVO.name)
pd.DataFrame(datos.clientes).head(8)
""")
code("""
m = datos.matriz
print(f"Matriz {m.km.shape[0]} × {m.km.shape[1]} km")
asimetricos = sum(1 for i in range(len(m.nodos)) for j in range(i + 1, len(m.nodos)) if abs(m.km[i, j] - m.km[j, i]) > 1e-9)
total_pares = len(m.nodos) * (len(m.nodos) - 1) // 2
print(f"Pares asimétricos: {asimetricos} de {total_pares}")
print(f"Ejemplo: 0→1 = {m.distancia(0, 1)} km, 1→0 = {m.distancia(1, 0)} km")
pd.DataFrame(m.km[:6, :6], index=m.nodos[:6], columns=m.nodos[:6])
""")
code("""
pd.DataFrame([vars(t) for t in datos.flota])
""")
code("""
demanda = pd.DataFrame({dia: pd.Series(datos.demanda[dia]) if datos.demanda[dia] is not None else pd.Series(dtype=float)
                        for dia in datos.dias}).reindex([c["nodo"] for c in datos.clientes if c["nodo"] != 0])
demanda.index.name = "Nodo"
demanda.fillna(0).sum().to_frame("Toneladas").T
""")

md("""
## 3. Validación

Antes de optimizar se revisan las hojas y columnas faltantes, los nodos de Demanda que no existen en Clientes o en la matriz,
una matriz no cuadrada o con vacíos, las demandas negativas o no numéricas, las coordenadas fuera de rango
y los pedidos mayores que la capacidad del vehículo más grande.

Un día con la columna vacía es un aviso, no un error. Ese día se omite.
""")
code("""
pd.DataFrame(problemas)
""")
md("Ejemplo con un archivo con errores (columna faltante, texto en una demanda y un nodo inexistente):")
code("""
_, errores = ruteo.preparar_datos("datos/pruebas/e_con_errores.xlsx")
pd.DataFrame(errores)[["hoja", "columna", "nodo", "problema"]]
""")
md("Resumen previo, antes de resolver: días con datos, clientes con pedido, toneladas y su relación con la capacidad de la flota.")
code("""
previo = ruteo.resumen_previo(datos)
print(f"Capacidad de la flota: {previo['capacidad_flota_t']} t en {previo['vehiculos_disponibles']} vehículos")
pd.DataFrame(previo["dias"])
""")

md(r"""
## 4. Modelo y método

**Conjuntos:** $N$ = clientes con demanda > 0 del día; $V$ = vehículos físicos (A1, B1, B2, C1, C2), cada uno con capacidad $Q_v$ y costo $c_v$ (COP/km).

**Objetivo:** $\min \sum_{v}\sum_{(i,j)} c_v \, d_{ij} \, x_{ijv}$, con $d_{ij}$ tomado de la matriz asimétrica en el sentido del tramo.
Un vehículo sin clientes no sale y no cuesta nada.

**Restricciones:**
1. Cada cliente se visita exactamente una vez, por un solo vehículo. Los pedidos no se dividen.
2. Cada ruta inicia y termina en el depot.
3. Carga de la ruta ≤ $Q_v$.
4. Tiempo de la ruta, contando viaje a 24 km/h, 30 min de descarga por cliente y regreso, ≤ 480 min.
5. Solo se usan los vehículos disponibles. El modelo decide cuántos salen.

**Implementación con OR-Tools Routing:**
- Escalas enteras: metros, kilogramos y segundos. La demanda y el tiempo se redondean hacia arriba y la capacidad hacia abajo, así el modelo entero nunca es más optimista que la realidad.
- Costo de arco distinto por vehículo: `SetArcCostEvaluatorOfVehicle` con metros × COP/km.
- Dimensión `Carga` con capacidad por vehículo.
- Dimensión `Tiempo`, cuyo tránsito i→j es la descarga en i más el viaje i→j, con tope de 480 min.

**Búsqueda, en fases:**
1. Se prueban cinco estrategias de solución inicial y se toma la más barata.
2. Desde ella, *Guided Local Search* mejora el plan hasta explorar 25.000 soluciones por día. Se detiene por número de iteraciones y no por reloj, así que el mismo archivo da el mismo plan en cualquier computador. Hay un tope de seguridad de 180 s.

**Respaldos si ninguna estrategia inicial encuentra un plan factible** (la capacidad está muy ajustada: 27,5 t contra 28 t):
- Modelo con penalización alta por cliente omitido. Siempre tiene punto de partida, y la metaheurística reduce las omisiones a cero.
- Asignación previa tipo *bin packing* con CP-SAT (capacidad + cota inferior de jornada), secuenciación por vehículo y cortes cuando un grupo no cabe en la jornada.

**Diagnóstico de días infactibles**, con cifras:
- Demanda total mayor que la capacidad de la flota.
- Pedidos que no caben sin dividirse, demostrado con CP-SAT.
- Jornada insuficiente: un cliente inalcanzable, o un reparto imposible con la cota de tiempo.
""")

md("## 5. Un día paso a paso: Lunes")
code("""
params = ruteo.parametros_por_defecto()
inst = ruteo.preparar_instancia(datos.demanda["Lunes"], datos.matriz, datos.flota, params)
print("Nodos del día:", len(inst["nodos"]) - 1, "clientes + depot")
print("Vehículos:", [v["id"] for v in inst["vehiculos"]])
print("Capacidades (kg):", inst["capacidad_kg"], "· Demanda total (kg):", sum(inst["demanda_kg"]))
print("Costo por vehículo (COP/km × 100):", inst["costo_x100"])
print("Tope de jornada (s):", inst["limite_s"])
""")
md("Fase 1: soluciones iniciales. Con 27,5 t contra 28 t, varias estrategias no encuentran un plan factible.")
code("""
iniciales = ruteo.fase_soluciones_iniciales(inst, params)
factibles = {e: costo / 1e5 for costo, e, _ in iniciales}
pd.DataFrame([{"Estrategia": e, "Factible": e in factibles, "Costo inicial (COP)": factibles.get(e)}
              for e in params["estrategias_iniciales"]])
""")
md("Respaldo con bin packing. Se ejecuta aquí solo para mostrarlo: en la corrida normal solo se usa si la fase 1 no encuentra nada.")
code("""
rutas_respaldo = ruteo.respaldo_empaque(inst, 10, params)
print("Respaldo factible:", rutas_respaldo is not None,
      "· costo de partida:", ruteo.formato_cop(ruteo._costo_entero(inst, rutas_respaldo) / 1e5) if rutas_respaldo else "-")
""")
md("Solución completa del Lunes con `resolver_ruteo`, la misma función que usa la aplicación:")
code("""
sol_lunes = ruteo.resolver_ruteo(datos.demanda["Lunes"], datos.matriz, datos.flota, params)
print(sol_lunes["estado"], sol_lunes["calculo"])
for r in sol_lunes["rutas"]:
    print(f"{r['vehiculo']}: 0 → {' → '.join(map(str, r['secuencia']))} → 0")
""")

md("""
## 6. Todos los días

`resolver_archivo` es la función de alto nivel. Valida el archivo, resuelve los días con datos en paralelo,
calcula indicadores y verifica. Devuelve una estructura serializable a JSON, la misma que consume el tablero.
""")
code("""
resultados = ruteo.resolver_archivo(ARCHIVO)
print(resultados["mensaje_final"])
ruteo.tabla_resumen(resultados)
""")

md("## 7. Detalle de rutas por día")
code("""
for d in resultados["dias"]:
    if d["estado"] != "resuelto":
        print(f"\\n{d['dia']}: {d['estado'].replace('_', ' ')}")
        continue
    print(f"\\n{d['dia']} — sin despachar: {', '.join(d['sin_despachar']) or 'ninguno'}")
    tabla = pd.DataFrame([{
        "Vehículo": r["vehiculo"], "Paradas": r["n_paradas"],
        "Carga (t)": round(r["carga_t"], 2), "Capacidad (t)": r["capacidad_t"], "% cap.": round(r["pct_capacidad"], 1),
        "Km": round(r["km"], 1), "Duración (min)": round(r["duracion_min"]), "% jornada": round(r["pct_jornada"], 1),
        "Regreso": r["hora_regreso"], "Costo (COP)": round(r["costo"]),
        "Secuencia": "0 → " + " → ".join(map(str, r["secuencia"])) + " → 0",
    } for r in d["rutas"]])
    display(tabla)
""")
md("Secuencia del vehículo más cargado del Lunes, con ETA y carga restante:")
code("""
lunes = next(d for d in resultados["dias"] if d["dia"] == "Lunes")
pd.DataFrame(lunes["rutas"][0]["paradas"])[["orden", "nodo", "cliente", "eta", "salida", "entrega_t", "carga_restante_t", "km_tramo"]]
""")

md("""
## 8. Verificación de restricciones

`verificar_solucion` recalcula todo desde la **matriz original**: visitas, inicio y fin en el depot, carga,
duración, flota y costo, respetando el sentido de cada tramo. La aplicación ejecuta esta misma función
antes de mostrar cualquier resultado.
""")
code("""
filas = []
for d in resultados["dias"]:
    if d["estado"] == "resuelto":
        for v in d["verificacion"]:
            filas.append({"Día": d["dia"], "Criterio": v["criterio"], "Cumple": "✔" if v["cumple"] else "✘", "Detalle": v["detalle"]})
verif = pd.DataFrame(filas)
print("Todas las restricciones se cumplen:", all(d.get("verificacion_ok", True) for d in resultados["dias"]))
verif
""")

md("""
## 9. Días infactibles: diagnóstico

Con el archivo de prueba `d_dia_infactible.xlsx`, donde el Viernes suma 30,5 t contra 28 t de flota,
el día se informa con su causa y los demás se resuelven normalmente. Para no repetir el cálculo completo,
esta celda usa 2.000 iteraciones por día (solo sirve para mostrar el diagnóstico).
""")
code("""
res_d = ruteo.resolver_archivo("datos/pruebas/d_dia_infactible.xlsx", parametros=ruteo.parametros_por_defecto(limite_iteraciones=2000))
print(res_d["mensaje_final"])
for d in res_d["dias"]:
    if d["estado"] == "infactible":
        print(f"{d['dia']}: {d['diagnostico']['titulo']} — {d['diagnostico']['mensaje']}")
""")

md("Resultados guardados para comparar con la aplicación, que debe dar los mismos costos para el mismo archivo:")
code("""
Path("salidas").mkdir(exist_ok=True)
Path("salidas/resultados_notebook.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=1), encoding="utf-8")
{d["dia"]: round(d["indicadores"]["costo_total"]) for d in resultados["dias"] if d["estado"] == "resuelto"}
""")

md("""
## 10. Cómo iniciar la aplicación

Desde una terminal, en la carpeta del proyecto:

```bash
pip install -r requirements.txt
python app.py
```

El navegador se abre en **http://127.0.0.1:8000** con el archivo de ejemplo ya resuelto.
Para resolver otros días, por ejemplo Viernes y Sábado, llene las columnas en Excel y use **Cargar archivo de Excel**.
No hay que tocar código.
""")

nb["cells"] = c
nb["metadata"]["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
nbf.write(nb, "ruteo_distribucion.ipynb")
print("ok")
