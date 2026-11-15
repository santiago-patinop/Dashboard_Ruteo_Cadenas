# Ruteo de distribución urbana — Valle de Aburrá

Este proyecto planea las rutas diarias de una flota heterogénea que sale del CD La Macarena (Medellín),
entrega pedidos a puntos de venta y regresa al mismo CD. Busca el plan de **mínimo costo**
(km × costo por km del vehículo) y respeta la capacidad, la jornada de 8 h y la flota disponible.

Tiene tres piezas que usan el mismo algoritmo:

| Archivo | Para qué sirve |
|---|---|
| `ruteo.py` | Contiene la configuración, la lectura y validación del Excel, el solver, el diagnóstico de días infactibles, la verificación, los indicadores y los exportes. |
| `ruteo_distribucion.ipynb` | Explica y ejecuta el proceso paso a paso con `ruteo.py`. |
| `app.py` + `static/` | Tablero web para el equipo de logística. |

## Arranque en dos pasos

Requiere Python 3.10 o superior.

```bash
pip install -r requirements.txt
```

```bash
python app.py
```

El navegador se abre en `http://127.0.0.1:8000` con el archivo de ejemplo ya resuelto.
Si el puerto 8000 está ocupado, la aplicación usa el siguiente libre y lo indica en la terminal.
Para detenerla, presione `Ctrl+C`.

## Uso del tablero

1. **Cargar archivo de Excel**: use el botón o arrastre el `.xlsx` a la ventana (máximo 5 MB). Se aceptan dos tipos de libro:
   - Un libro completo con las hojas `Clientes`, `Matriz_Distancias`, `Demanda` y `Vehiculos`.
   - Un libro que solo trae la hoja `Demanda`. En ese caso se usan los clientes, la matriz y la flota del archivo base, y el tablero lo indica.
2. **Validación**: si hay errores, se listan con hoja, columna, nodo y problema, y no se calcula nada.
   El tablero conserva el último resultado válido.
3. **Resumen y cálculo**: el tablero muestra los días detectados, los clientes, las toneladas por día y la capacidad de la flota.
   Luego resuelve los días en paralelo, y cada día aparece apenas termina.
4. **Resultados**: hay un selector de día, cinco indicadores, el mapa, la tabla por ruta y el costo por día.
   Al hacer clic en una fila, la ruta se aísla en el mapa y se ven sus paradas con la hora estimada de llegada (ETA) y la carga restante.
5. **Descargas**:
   - **Descargar plantilla**: libro listo para llenar.
   - **Descargar rutas**: `rutas.xlsx` con el resumen y una hoja de paradas por día.
   - **Exportar tablero**: HTML autocontenido para compartir. Solo el mapa base requiere internet.

Para resolver Viernes y Sábado basta llenar esas columnas en la hoja `Demanda` y cargar el archivo. No hay que tocar código.

### Reglas del archivo

- Los días son las columnas a la derecha de `Cliente`, con cualquier nombre.
- Una columna vacía es un día **sin datos**: se omite y se informa.
- Una demanda vacía o en 0 significa que el cliente no se visita ese día. Se acepta coma o punto decimal.
- En `Vehiculos`, la capacidad y el costo pueden venir como texto (`10 toneladas`, `4.200 COP/km`). El punto es separador de miles.
- La matriz de distancias es asimétrica. La fila es el origen y la columna el destino, y se usa tal como viene.

## Método

El solver es **OR-Tools Routing** (HFVRP):
- **Costo:** cada vehículo tiene su propio costo de arco (metros × COP/km).
- **Capacidad:** una dimensión de carga en kg.
- **Tiempo:** una dimensión de tiempo en segundos, donde cada tramo suma la descarga de 30 min más el viaje a 24 km/h. El tope es de 480 min, incluido el regreso.
- **Escalas enteras:** la demanda y el tiempo se redondean hacia arriba y la capacidad hacia abajo. Así el modelo nunca es más optimista que la realidad.

La búsqueda tiene cuatro pasos:
1. Prueba 5 estrategias de solución inicial y toma la de menor costo.
2. Mejora esa solución con *Guided Local Search* hasta el tiempo límite (30 s por día, configurable en `CONFIG`).
3. Si ninguna estrategia encuentra un plan factible, aplica dos respaldos en orden:
   - Un modelo con penalización alta por cliente omitido. Solo se acepta si al final atiende a todos.
   - Una asignación previa tipo *bin packing* con CP-SAT, que considera capacidad y una cota de jornada, con cortes cuando un grupo de clientes no cabe en una jornada.
4. Si aun así no hay plan, diagnostica la causa con cifras:
   - La demanda supera la capacidad total.
   - Los pedidos no caben sin dividirlos (lo demuestra CP-SAT).
   - La jornada es insuficiente.

   El día se muestra con el diagnóstico y sin rutas.

Antes de mostrar un día, `verificar_solucion` recalcula todo desde la matriz original:
- Cada cliente se visita una vez.
- Toda ruta inicia y termina en el depot.
- Se cumplen la carga, la duración y la flota.
- El costo coincide, tramo por tramo y en su sentido.

## Configuración

Todos los parámetros están en `ruteo.CONFIG`:
- Operación: velocidad, descarga, inicio y duración de la jornada.
- Solver: tiempo límite y estrategias.
- Tablero: umbrales de alerta (90 %) y de subutilización (50 %).
- Archivos: tamaño máximo y archivo base.

## Estructura

```
ruteo.py                         Algoritmo (única implementación)
ruteo_distribucion.ipynb         Notebook explicativo, ya ejecutado
app.py                           Servidor FastAPI
static/                          index.html, app.css, app.js y vendor/ (Leaflet 1.9.4, Chart.js 4.4.6 locales)
datos/Reto_VRP_Medellin_Datos.xlsx   Archivo base
datos/pruebas/                   Archivos de prueba: a) original, b) Viernes y Sábado llenos, c) solo Demanda,
                                 d) día infactible, e) con errores, f) archivos que no son Excel
scripts/                         Generadores de los archivos de prueba y del notebook
```

## Supuestos

- Todos los vehículos salen a las 07:00 y no hay ventanas horarias ni esperas. La ETA es la hora de llegada al cliente.
- Solo hay costo variable por km. Un vehículo que no sale no cuesta nada.
- Cada vehículo hace una sola vuelta por día.
- Los pedidos no se dividen.
- Las coordenadas solo se usan para dibujar el mapa. Los km salen de la matriz.
- Los umbrales del semáforo se comparan con el porcentaje redondeado que se muestra.

## Limitaciones

- La búsqueda es heurística con tiempo límite, así que no garantiza el óptimo.
  En el archivo de ejemplo, correr 120 s por día da los mismos costos que 30 s.
- Un día "sin solución en el tiempo límite" no siempre es imposible. El diagnóstico lo dice cuando no se pudo demostrar.
- El trazado del mapa es en línea recta entre paradas y solo sirve de referencia.
- El mapa base (CARTO/OpenStreetMap) requiere internet. El resto del tablero funciona sin conexión.
- Los resultados se guardan en memoria mientras la aplicación está abierta. Los archivos subidos no se escriben en disco.
