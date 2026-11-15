"""
vista_colab.py — Presentación de resultados en notebooks (Google Colab o Jupyter).

Solo dibuja: indicadores, tablas, mapa y gráfico a partir de la estructura que devuelve
ruteo.resolver_archivo. No contiene lógica de ruteo.
"""

from __future__ import annotations

import html
import math

import pandas as pd

from ruteo import formato_cop, formato_num

# Mismos colores de ruta del tablero (Okabe-Ito, aptos para daltonismo).
COLORES_RUTA = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#7A5195", "#8C6D31", "#4D4D4D", "#C0392B"]
TEXTO_OSCURO = {"#E69F00", "#56B4E9"}

ESTILO = """
<style>
.rt{font-family:system-ui,-apple-system,'Segoe UI',Roboto,Arial,sans-serif;font-size:14px;color:#171c26}
.rt .kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin:8px 0 12px}
.rt .kpi{background:#fff;border:1px solid #e1e4ea;border-radius:10px;padding:8px 12px}
.rt .kt{font-size:12.5px;color:#5b6475;font-weight:600}
.rt .kv{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums;white-space:nowrap}
.rt .kv small{font-size:14px;color:#444c5c;font-weight:600;margin-left:3px}
.rt .kd{font-size:12.5px;color:#444c5c}
.rt table.t{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;margin:4px 0 10px;table-layout:auto!important}
.rt table.t th{text-align:left;font-size:12.5px;color:#5b6475;border-bottom:1px solid #c9ced8;padding:5px 8px;background:#f7f8fa}
.rt table.t td{border-bottom:1px solid #e1e4ea;padding:5px 8px;white-space:nowrap;text-align:left;max-width:none}
.rt table.t td.w{white-space:normal}
.rt table.t td.n,.rt table.t th.n{text-align:right}
.rt .sem{font-weight:700;font-size:12.5px;padding:0 6px;border-radius:99px}
.rt .sem.ok{color:#157a3c;background:#e6f4ea}.rt .sem.al{color:#b42318;background:#fdecea}.rt .sem.ba{color:#8a5a00;background:#fff4d6}
.rt .bar{height:6px;background:#e6e9ee;border-radius:99px;overflow:hidden;width:110px;margin-top:2px}
.rt .bar i{display:block;height:100%;background:#157a3c}.rt .bar.al i{background:#b42318}.rt .bar.ba i{background:#a87000}
.rt .sw{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:-1px}
.rt .diag{background:#fdecea;border:1px solid #b42318;border-radius:10px;padding:10px 14px;margin:8px 0}
.rt .diag b{color:#b42318}
.rt .nota{color:#5b6475;font-size:12.5px}
.rt details{margin:4px 0}.rt summary{cursor:pointer;font-weight:600}
.rt .tabs>input{display:none}
.rt .tabs>label{display:inline-block;padding:7px 14px;margin:0 2px 0 0;border-radius:8px 8px 0 0;background:#eef0f4;cursor:pointer;font-weight:600;color:#444c5c}
.rt .tabs>label.off{color:#8a919e;cursor:not-allowed}
/* Paneles ocultos fuera de pantalla (no display:none) para que los mapas se inicialicen con su tamaño real. */
.rt .tabs{position:relative;overflow:hidden}
.rt .tabs>.panel{position:absolute;left:-20000px;top:0;width:100%;border-top:2px solid #1d4ed8;padding-top:6px}
.rt .num{display:inline-block;min-width:20px;height:20px;line-height:20px;border-radius:50%;text-align:center;font-size:11.5px;font-weight:700}
</style>
"""


def _e(texto) -> str:
    return html.escape(str(texto if texto is not None else ""))


def color_ruta(resultados: dict, vehiculo: str) -> str:
    """Color fijo por vehículo (el mismo en todos los días)."""
    ids = resultados.get("vehiculos", [])
    return COLORES_RUTA[(ids.index(vehiculo) if vehiculo in ids else 0) % len(COLORES_RUTA)]


def _semaforo(pct: float, umbral_alerta: float, umbral_bajo: float | None = None) -> tuple[str, str]:
    """Clase CSS y etiqueta del semáforo (con ícono, no solo color)."""
    p = round(pct)
    if p >= umbral_alerta:
        return "al", f"▲ {p} %"
    if umbral_bajo is not None and p < umbral_bajo:
        return "ba", f"▼ {p} %"
    return "ok", f"● {p} %"


def tarjetas_kpi(resultados: dict, dia: dict) -> str:
    """Los 5 indicadores del día en tarjetas."""
    i = dia["indicadores"]
    par = resultados["parametros"]
    por_tipo = " · ".join(f"{t['codigo']} {t['usados']}/{t['disponibles']}" for t in i["por_tipo"])
    holgura = int(math.floor(i["holgura_min"]))
    clase, _ = _semaforo(100 - 100 * i["holgura_min"] / par["jornada_max_min"], par["umbral_alerta_pct"])
    tarjetas = [
        ("Costo total del plan", formato_cop(i["costo_total"]), f"{formato_cop(i['costo_por_t'])} por tonelada · {formato_num(i['toneladas'], 1)} t"),
        ("Distancia total", f"{formato_num(i['km_total'], 1)}<small>km</small>", f"{formato_cop(i['costo_por_km'])} por km en promedio"),
        ("Vehículos usados", f"{i['vehiculos_usados']}<small>/ {i['vehiculos_disponibles']}</small>", _e(por_tipo)),
        ("Utilización de capacidad", f"{formato_num(i['utilizacion_pct'], 1)}<small>%</small>",
         f"{formato_num(i['toneladas'], 1)} t de {formato_num(i['capacidad_usada_t'], 1)} t en vehículos usados"),
        ("Regreso más tardío", f"{_e(i['regreso_max'])}<small>{_e(i['vehiculo_regreso_max'])}</small>",
         f"<span class='sem {clase}'>{'▲' if clase == 'al' else '●'} {holgura} min</span> de holgura vs. {_e(i['hora_limite'])}"),
    ]
    celdas = "".join(f"<div class='kpi'><div class='kt'>{t}</div><div class='kv'>{v}</div><div class='kd'>{d}</div></div>"
                     for t, v, d in tarjetas)
    confirmacion = (f"<div class='nota'>✓ Clientes atendidos {i['clientes_atendidos']}/{i['clientes_con_demanda']} · "
                    f"{'✓ Restricciones verificadas' if dia.get('verificacion_ok') else '✗ Revisar verificación'}</div>")
    return f"<div class='kpis'>{celdas}</div>{confirmacion}"


def tabla_rutas(resultados: dict, dia: dict) -> str:
    """Tabla por vehículo con barras y semáforo de capacidad y jornada, y la secuencia de paradas."""
    par = resultados["parametros"]
    filas, detalles = [], []
    for r in dia["rutas"]:
        color = color_ruta(resultados, r["vehiculo"])
        cc, ec = _semaforo(r["pct_capacidad"], par["umbral_alerta_pct"], par["umbral_subutilizado_pct"])
        cj, ej = _semaforo(r["pct_jornada"], par["umbral_alerta_pct"])
        horas = int(round(r["duracion_min"]))
        filas.append(
            f"<tr><td><span class='sw' style='background:{color}'></span><b>{_e(r['vehiculo'])}</b> · Tipo {_e(r['codigo'])}</td>"
            f"<td class='n'>{r['n_paradas']}</td>"
            f"<td>{formato_num(r['carga_t'], 1)} / {formato_num(r['capacidad_t'], 1)} t <span class='sem {cc}'>{ec}</span>"
            f"<div class='bar {cc if cc != 'ok' else ''}'><i style='width:{min(100, r['pct_capacidad']):.0f}%'></i></div></td>"
            f"<td>{horas // 60} h {horas % 60:02d} min <span class='sem {cj}'>{ej}</span>"
            f"<div class='bar {cj if cj != 'ok' else ''}'><i style='width:{min(100, r['pct_jornada']):.0f}%'></i></div></td>"
            f"<td class='n'>{formato_num(r['km'], 1)}</td><td class='n'>{_e(r['hora_regreso'])}</td>"
            f"<td class='n'>{formato_cop(r['costo'])}</td></tr>")
        texto = "#fff" if color not in TEXTO_OSCURO else "#111"
        paradas = "".join(
            f"<tr><td><span class='num' style='background:{color};color:{texto}'>{p['orden']}</span></td><td>{_e(p['cliente'])}</td>"
            f"<td>{_e(p['direccion'])}</td><td>{_e(p['eta'])}</td><td class='n'>{formato_num(p['entrega_t'], 1)} t</td>"
            f"<td class='n'>{formato_num(p['carga_restante_t'], 1)} t</td></tr>" for p in r["paradas"])
        detalles.append(
            f"<details><summary><span class='sw' style='background:{color}'></span>{_e(r['vehiculo'])}: "
            f"{r['n_paradas']} paradas · regreso {_e(r['hora_regreso'])}</summary>"
            f"<table class='t'><tr><th>#</th><th>Cliente</th><th>Dirección</th><th>ETA</th><th class='n'>Entrega</th><th class='n'>Carga restante</th></tr>"
            f"<tr><td></td><td><i>Salida del depot</i></td><td></td><td>{_e(par['inicio_jornada'])}</td><td></td>"
            f"<td class='n'>{formato_num(r['carga_t'], 1)} t</td></tr>{paradas}"
            f"<tr><td></td><td><i>Regreso al depot</i></td><td></td><td>{_e(r['hora_regreso'])}</td><td></td><td class='n'>0 t</td></tr></table></details>")
    sin = ", ".join(dia["sin_despachar"]) or "ninguno"
    return (f"<table class='t'><tr><th>Vehículo</th><th class='n'>Paradas</th><th>Carga / capacidad</th><th>Jornada</th>"
            f"<th class='n'>Km</th><th class='n'>Regreso</th><th class='n'>Costo</th></tr>{''.join(filas)}</table>"
            f"<div>Sin despachar: <b>{_e(sin)}</b></div><div style='margin-top:6px'><b>Secuencia de paradas</b> (clic para desplegar)</div>"
            + "".join(detalles))


def mapa_folium(resultados: dict, dia: dict, alto: int = 460):
    """Mapa del día: depot, rutas con sentido, clientes numerados y clientes sin pedido en gris."""
    import folium
    from folium.plugins import PolyLineTextPath

    clientes = {c["nodo"]: c for c in resultados["clientes"]}
    depot = resultados["depot"]
    puntos = [(c["lat"], c["lon"]) for c in resultados["clientes"]]
    m = folium.Map(tiles=None, control_scale=True, zoom_snap=0.25)
    folium.TileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", name="Mapa base", control=False,
                     attr='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; '
                          '<a href="https://carto.com/attributions">CARTO</a>', subdomains="abcd", max_zoom=19).add_to(m)
    m.fit_bounds([[min(p[0] for p in puntos), min(p[1] for p in puntos)], [max(p[0] for p in puntos), max(p[1] for p in puntos)]],
                 padding=(24, 24))
    en_ruta = set()
    for r in dia.get("rutas", []):
        color = color_ruta(resultados, r["vehiculo"])
        grupo = folium.FeatureGroup(name=f"{r['vehiculo']} · {r['n_paradas']} paradas")
        traza = [(depot["lat"], depot["lon"])] + [(clientes[p["nodo"]]["lat"], clientes[p["nodo"]]["lon"]) for p in r["paradas"]] + [(depot["lat"], depot["lon"])]
        folium.PolyLine(traza, color="#ffffff", weight=7, opacity=0.9).add_to(grupo)
        linea = folium.PolyLine(traza, color=color, weight=3.5, opacity=1).add_to(grupo)
        PolyLineTextPath(linea, "     ►     ", repeat=True, offset=5, attributes={"fill": color, "font-size": "13"}).add_to(grupo)
        texto = "#fff" if color not in TEXTO_OSCURO else "#111"
        for p in r["paradas"]:
            c = clientes[p["nodo"]]
            en_ruta.add(p["nodo"])
            info = (f"<b>{_e(c['nombre'])}</b><br><span style='color:#5b6475'>{_e(c['direccion'])}</span><br>"
                    f"Demanda: {formato_num(p['entrega_t'], 2)} t<br>Vehículo: {_e(r['vehiculo'])}<br>"
                    f"Parada {p['orden']} de {r['n_paradas']} · ETA {_e(p['eta'])}")
            folium.Marker((c["lat"], c["lon"]), tooltip=folium.Tooltip(info), icon=folium.DivIcon(
                icon_size=(22, 22), icon_anchor=(11, 11),
                html=f"<div style='width:22px;height:22px;border-radius:50%;background:{color};color:{texto};border:2px solid #fff;"
                     f"font:700 11px/18px system-ui,sans-serif;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.4)'>{p['orden']}</div>")).add_to(grupo)
        grupo.add_to(m)
    demanda = dia.get("demanda", {})
    for nodo, c in clientes.items():
        if nodo == depot["nodo"] or nodo in en_ruta:
            continue
        pedido = demanda.get(str(nodo))
        etiqueta = f"<b>{_e(c['nombre'])}</b><br>" + (f"Demanda {formato_num(pedido, 2)} t · sin ruta" if pedido else "Sin pedido este día")
        folium.CircleMarker((c["lat"], c["lon"]), radius=5 if not pedido else 7, color="#8b93a1" if not pedido else "#444c5c",
                            fill=True, fill_opacity=0.45 if not pedido else 0.9, tooltip=etiqueta).add_to(m)
    folium.Marker((depot["lat"], depot["lon"]), tooltip=f"CD {_e(depot['nombre'])}", icon=folium.DivIcon(
        icon_size=(30, 30), icon_anchor=(15, 15),
        html="<div style='width:30px;height:30px;border-radius:7px;background:#171c26;color:#fff;border:2px solid #fff;"
             "font:700 11px/26px system-ui,sans-serif;text-align:center;box-shadow:0 2px 6px rgba(0,0,0,.4)'>CD</div>")).add_to(m)
    if dia.get("rutas"):
        folium.LayerControl(collapsed=False).add_to(m)
    return m


def _iframe_mapa(mapa, alto: int) -> str:
    """Mapa de folium en un iframe de alto fijo (sin el espacio extra del render por defecto)."""
    documento = mapa.get_root().render()
    return (f"<iframe srcdoc=\"{html.escape(documento)}\" style='width:100%;height:{alto + 4}px;border:1px solid #e1e4ea;"
            f"border-radius:8px' loading='eager'></iframe>")


def html_dias(resultados: dict, alto_mapa: int = 460) -> str:
    """Pestañas por día con indicadores, mapa y tabla (o diagnóstico / sin datos)."""
    pestanas, paneles, css_activo = [], [], []
    primero = next((d["dia"] for d in resultados["dias"] if d["estado"] in ("resuelto", "infactible", "error")), None)
    for k, d in enumerate(resultados["dias"]):
        ident = f"tab{k}"
        if d["estado"] == "sin_datos":
            pestanas.append(f"<label class='off' title='Columna vacía'>{_e(d['dia'])} · Sin datos</label>")
            continue
        icono = " ⚠" if d["estado"] in ("infactible", "error") else ""
        marcado = "checked" if d["dia"] == primero else ""
        pestanas.append(f"<input type='radio' name='dias' id='{ident}' {marcado}><label for='{ident}'>{_e(d['dia'])}{icono}</label>")
        css_activo.append(f"#{ident}:checked~#p{ident}{{position:static}} #{ident}:checked+label{{background:#1d4ed8;color:#fff}}")
        if d["estado"] == "resuelto":
            cuerpo = (tarjetas_kpi(resultados, d) + _iframe_mapa(mapa_folium(resultados, d, alto_mapa), alto_mapa)
                      + "<div class='nota'>Trazado en línea recta entre paradas (referencial). Los km provienen de la matriz de distancias viales.</div>"
                      + tabla_rutas(resultados, d))
        else:
            g = d.get("diagnostico") or {"titulo": "No fue posible calcular el día", "mensaje": d.get("mensaje", "")}
            cuerpo = (f"<div class='diag'><b>⚠ {_e(d['dia'])}: {_e(g['titulo'])}</b><br>{_e(g['mensaje'])}</div>"
                      f"<div class='nota'>No se publican rutas para este día. Clientes con pedido en el mapa (punto oscuro).</div>"
                      + _iframe_mapa(mapa_folium(resultados, d, alto_mapa), alto_mapa))
        paneles.append(f"<div class='panel' id='p{ident}'>{cuerpo}</div>")
    return (ESTILO + f"<style>{''.join(css_activo)}</style><div class='rt'><div class='tabs'>"
            + "".join(pestanas) + "".join(paneles) + "</div></div>")


def tabla_resumen_html(resultados: dict) -> str:
    """Tabla semanal con formato colombiano."""
    filas = []
    for d in resultados["dias"]:
        if d["estado"] == "resuelto":
            i = d["indicadores"]
            filas.append(f"<tr><td><b>{_e(d['dia'])}</b></td><td>✓ Resuelto</td><td class='n'>{formato_num(d['demanda_total_t'], 1)}</td>"
                         f"<td class='n'>{formato_cop(i['costo_total'])}</td><td class='n'>{formato_cop(i['costo_por_t'])}</td>"
                         f"<td class='n'>{formato_num(i['km_total'], 1)}</td><td class='n'>{i['vehiculos_usados']}/{i['vehiculos_disponibles']}</td>"
                         f"<td class='n'>{formato_num(i['utilizacion_pct'], 1)} %</td><td class='n'>{_e(i['regreso_max'])}</td>"
                         f"<td class='n'>{int(math.floor(i['holgura_min']))} min</td></tr>")
        elif d["estado"] == "sin_datos":
            filas.append(f"<tr><td><b>{_e(d['dia'])}</b></td><td style='color:#5b6475'>Sin datos</td>" + "<td></td>" * 8 + "</tr>")
        else:
            g = d.get("diagnostico", {}).get("titulo", d.get("mensaje", ""))
            filas.append(f"<tr><td><b>{_e(d['dia'])}</b></td><td class='w' style='color:#b42318'>⚠ {_e(g)}</td>"
                         f"<td class='n'>{formato_num(d.get('demanda_total_t', 0), 1)}</td>" + "<td></td>" * 7 + "</tr>")
    return (ESTILO + "<div class='rt'><table class='t'><tr><th>Día</th><th>Estado</th><th class='n'>Demanda (t)</th>"
            "<th class='n'>Costo</th><th class='n'>COP por t</th><th class='n'>Km</th><th class='n'>Vehículos</th>"
            "<th class='n'>Utilización</th><th class='n'>Regreso más tardío</th><th class='n'>Holgura</th></tr>"
            + "".join(filas) + "</table></div>")


def grafico_costos(resultados: dict):
    """Barras de costo total por día resuelto, con las toneladas como etiqueta."""
    import matplotlib.pyplot as plt

    dias = [d for d in resultados["dias"] if d["estado"] == "resuelto"]
    fig, ax = plt.subplots(figsize=(8, 2.8), dpi=110)
    if not dias:
        ax.text(0.5, 0.5, "No hay días resueltos", ha="center", va="center")
        ax.axis("off")
        return fig
    costos = [d["indicadores"]["costo_total"] for d in dias]
    por_t = [d["indicadores"]["costo_por_t"] for d in dias]
    maximo = max(por_t)
    colores = ["#1d4ed8" if p == maximo else "#a9bdf2" for p in por_t]
    barras = ax.bar([d["dia"] for d in dias], costos, color=colores, width=0.55)
    for b, d in zip(barras, dias):
        ax.annotate(f"{formato_num(d['indicadores']['toneladas'], 1)} t", (b.get_x() + b.get_width() / 2, b.get_height()),
                    xytext=(0, 3), textcoords="offset points", ha="center", fontsize=9, color="#444c5c", fontweight="bold")
    ax.set_ylim(0, max(costos) * 1.18)
    ax.yaxis.set_major_formatter(lambda v, _: formato_cop(v))
    ax.spines[["top", "right"]].set_visible(False)
    ax.tick_params(labelsize=9)
    ax.set_title("Costo total por día (etiqueta: toneladas). Barra oscura = mayor costo por tonelada", fontsize=10, loc="left")
    fig.tight_layout()
    return fig


def tabla_verificacion(resultados: dict) -> pd.DataFrame:
    """Cumplimiento de restricciones por día (✔ / ✘)."""
    filas = []
    for d in resultados["dias"]:
        for v in d.get("verificacion", []):
            filas.append({"Día": d["dia"], "Criterio": v["criterio"], "Cumple": "✔" if v["cumple"] else "✘", "Detalle": v["detalle"]})
    return pd.DataFrame(filas)


def tabla_problemas(problemas: list) -> pd.DataFrame:
    """Errores o avisos de validación como tabla (hoja, columna, nodo, problema)."""
    return pd.DataFrame([{"Hoja": p["hoja"] or "—", "Columna": p["columna"] or "—", "Nodo": p["nodo"] or "—",
                          "Problema": p["problema"]} for p in problemas])
