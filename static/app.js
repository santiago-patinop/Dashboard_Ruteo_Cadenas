/* ==========================================================================
   Tablero de rutas — lógica de interfaz
   Consume la estructura JSON de ruteo.resolver_archivo / app.py.
   ========================================================================== */
(function () {
  'use strict';

  // ---------------------------------------------------------------- Constantes
  const API = {
    inicial: '/api/inicial',
    cargar: '/api/cargar',
    trabajo: (id) => '/api/trabajo/' + encodeURIComponent(id),
    plantilla: '/api/plantilla',
    rutas: '/api/rutas.xlsx',
    exportar: '/api/exportar',
  };
  const EXPORTADO = window.DATOS_EXPORTADOS || null;
  const TAMANO_MAX_MB = 5;

  // Colores de ruta (independientes de la paleta de interfaz). Basados en Okabe-Ito,
  // aptos para daltonismo; variante más luminosa en modo oscuro para contrastar con el mapa.
  const COLORES_RUTA = {
    claro:  ['#0072B2', '#D55E00', '#009E73', '#CC79A7', '#E69F00', '#56B4E9', '#7A5195', '#8C6D31', '#4D4D4D', '#C0392B'],
    oscuro: ['#4AA8F0', '#FF8A4C', '#2BD4A0', '#F09AD0', '#F5B83D', '#9BD7FF', '#B79CE8', '#D9B86C', '#C8C8C8', '#FF6B6B'],
  };
  const PALETAS = [
    { id: 'azul', nombre: 'Azul' },
    { id: 'turquesa', nombre: 'Turquesa' },
    { id: 'violeta', nombre: 'Violeta' },
    { id: 'granate', nombre: 'Granate' },
    { id: 'grafito', nombre: 'Grafito' },
  ];
  const MAPAS_BASE = {
    claro: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    oscuro: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  };
  const ATRIBUCION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>';

  // ---------------------------------------------------------------- Estado
  const estado = {
    resultados: null,      // resultado mostrado (válido)
    dia: null,             // día seleccionado
    rutaAislada: null,     // id de vehículo aislado
    rutasOcultas: new Set(),
    trabajo: null,         // id de trabajo en curso
    sondeo: null,
    progreso: null,
    claveRender: '',
    panelCargaAbierto: false,
  };

  const $ = (sel) => document.querySelector(sel);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---------------------------------------------------------------- Formato colombiano
  function fmtNum(v, dec) {
    if (v == null || isNaN(v)) return '–';
    dec = dec || 0;
    const neg = v < 0;
    const partes = Math.abs(v).toFixed(dec).split('.');
    const entero = partes[0].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return (neg ? '−' : '') + entero + (partes[1] ? ',' + partes[1] : '');
  }
  const fmtCOP = (v) => '$ ' + fmtNum(Math.round(v));
  const fmtT = (v, dec) => fmtNum(v, dec == null ? 1 : dec) + ' t';
  function fmtDur(min) {
    const t = Math.round(min);
    return Math.floor(t / 60) + ' h ' + String(t % 60).padStart(2, '0') + ' min';
  }
  function fmtFecha(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    const p = (n) => String(n).padStart(2, '0');
    return p(d.getDate()) + '/' + p(d.getMonth() + 1) + '/' + d.getFullYear() + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }
  const tema = () => document.documentElement.getAttribute('data-theme') === 'oscuro' ? 'oscuro' : 'claro';
  const css = (nombre) => getComputedStyle(document.documentElement).getPropertyValue(nombre).trim();

  function colorRuta(idVehiculo) {
    const lista = (estado.resultados && estado.resultados.vehiculos) || [];
    const i = Math.max(0, lista.indexOf(idVehiculo));
    const colores = COLORES_RUTA[tema()];
    return colores[i % colores.length];
  }
  function textoSobre(hex) {
    const n = parseInt(hex.slice(1), 16);
    const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); });
    const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
    return L > 0.22 ? '#111' : '#fff';
  }

  // ---------------------------------------------------------------- Preferencias
  function guardarPreferencia(clave, valor) { try { localStorage.setItem(clave, valor); } catch (e) { /* sin almacenamiento */ } }

  function iniciarApariencia() {
    const contenedor = $('#paletas');
    const actual = document.documentElement.getAttribute('data-paleta') || 'azul';
    contenedor.innerHTML = PALETAS.map((p) =>
      `<button type="button" class="paleta" role="radio" data-paleta-opcion="${p.id}" aria-checked="${p.id === actual}" aria-label="Paleta ${p.nombre}" title="${p.nombre}"></button>`).join('');
    contenedor.querySelectorAll('.paleta').forEach((b) => {
      // Muestra el color de la paleta en el tema actual.
      b.setAttribute('data-paleta', b.dataset.paletaOpcion);
      b.style.background = 'var(--acento-muestra)';
      b.addEventListener('click', () => cambiarPaleta(b.dataset.paletaOpcion));
    });
    $('#btn-tema').addEventListener('click', () => cambiarTema(tema() === 'oscuro' ? 'claro' : 'oscuro'));
    actualizarBotonTema();
  }
  function cambiarPaleta(id) {
    document.documentElement.setAttribute('data-paleta', id);
    guardarPreferencia('ruteo.paleta', id);
    document.querySelectorAll('.paleta').forEach((b) => b.setAttribute('aria-checked', String(b.dataset.paletaOpcion === id)));
    actualizarGrafico();
  }
  function cambiarTema(nuevo) {
    document.documentElement.setAttribute('data-theme', nuevo);
    guardarPreferencia('ruteo.tema', nuevo);
    actualizarBotonTema();
    if (capaBase) capaBase.setUrl(MAPAS_BASE[nuevo]);
    renderizarDia(true);
  }
  function actualizarBotonTema() {
    $('#btn-tema').setAttribute('aria-label', tema() === 'oscuro' ? 'Cambiar a modo claro' : 'Cambiar a modo oscuro');
  }

  // ---------------------------------------------------------------- Mapa
  let mapa = null, capaBase = null, capaRutas = null, capaGris = null, capaFlechas = null, capaDepot = null, leyenda = null;
  const capasPorRuta = {};

  function iniciarMapa() {
    mapa = L.map('mapa', { zoomControl: true, attributionControl: true, zoomSnap: 0.25 });
    mapa.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
    capaBase = L.tileLayer(MAPAS_BASE[tema()], { attribution: ATRIBUCION, subdomains: 'abcd', maxZoom: 19 }).addTo(mapa);
    capaGris = L.layerGroup().addTo(mapa);
    capaRutas = L.layerGroup().addTo(mapa);
    capaFlechas = L.layerGroup().addTo(mapa);
    capaDepot = L.layerGroup().addTo(mapa);
    mapa.setView([6.25, -75.58], 11);
    mapa.on('zoomend', dibujarFlechas);
    leyenda = L.control({ position: 'topright' });
    leyenda.onAdd = function () {
      const div = L.DomUtil.create('div', 'leyenda');
      L.DomEvent.disableClickPropagation(div);
      L.DomEvent.disableScrollPropagation(div);
      return div;
    };
    leyenda.addTo(mapa);
    let espera = null;
    new ResizeObserver(() => {
      mapa.invalidateSize();
      clearTimeout(espera);
      espera = setTimeout(() => { if (estado.resultados) reencuadrar(); }, 120);
    }).observe($('#mapa'));
  }

  function ajustarVista(puntos) {
    const validos = puntos.filter((p) => p && p[0] != null && p[1] != null);
    if (!validos.length) return;
    const ancho = $('#mapa').clientWidth;
    const derecha = leyenda && ancho > 520 ? 210 : 30;
    mapa.fitBounds(L.latLngBounds(validos), { paddingTopLeft: [110, 44], paddingBottomRight: [derecha, 34], maxZoom: 15 });
  }

  function reencuadrar() {
    if (estado.rutaAislada && capasPorRuta[estado.rutaAislada]) ajustarVista(capasPorRuta[estado.rutaAislada].puntos);
    else ajustarVista(puntosTodos());
  }

  function puntosTodos() {
    return (estado.resultados.clientes || []).map((c) => [c.lat, c.lon]);
  }

  function infoCliente(c, extra) {
    const filas = extra.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('');
    return `<b>${esc(c.nombre)}</b><span class="dir">${esc(c.direccion || '')}</span><dl>${filas}</dl>`;
  }

  function dibujarMapa(dia) {
    capaRutas.clearLayers(); capaGris.clearLayers(); capaDepot.clearLayers();
    Object.keys(capasPorRuta).forEach((k) => delete capasPorRuta[k]);
    const r = estado.resultados;
    if (!r) return;
    const clientes = {};
    r.clientes.forEach((c) => { clientes[c.nodo] = c; });
    const depot = r.depot;
    const enRuta = new Set();

    if (dia && dia.estado === 'resuelto') {
      dia.rutas.forEach((ruta) => {
        const color = colorRuta(ruta.vehiculo);
        const grupo = L.layerGroup();
        const puntos = [[depot.lat, depot.lon]].concat(ruta.paradas.map((p) => [clientes[p.nodo].lat, clientes[p.nodo].lon]), [[depot.lat, depot.lon]]);
        L.polyline(puntos, { color: css('--halo'), weight: 7, opacity: 0.9, interactive: false }).addTo(grupo);
        L.polyline(puntos, { color: color, weight: 3.5, opacity: 1, interactive: false }).addTo(grupo);
        ruta.paradas.forEach((p) => {
          enRuta.add(p.nodo);
          const c = clientes[p.nodo];
          const icono = L.divIcon({
            className: '', iconSize: [22, 22], iconAnchor: [11, 11],
            html: `<div class="marcador-parada" style="background:${color};color:${textoSobre(color)}">${p.orden}</div>`,
          });
          const contenido = infoCliente(c, [
            ['Demanda', fmtT(p.entrega_t, 2)],
            ['Vehículo', esc(ruta.vehiculo) + ' · ' + esc(ruta.tipo)],
            ['Parada', p.orden + ' de ' + ruta.n_paradas],
            ['ETA', esc(p.eta)],
            ['Carga restante', fmtT(p.carga_restante_t, 2)],
          ]);
          L.marker([c.lat, c.lon], { icon: icono, keyboard: false, riseOnHover: true })
            .bindTooltip(contenido, { className: 'info', direction: 'auto', offset: [8, 0] })
            .on('click', () => seleccionarRuta(ruta.vehiculo))
            .addTo(grupo);
        });
        capasPorRuta[ruta.vehiculo] = { grupo, puntos, color };
      });
    }

    // Clientes sin pedido (gris tenue, sin número) y, en días infactibles, los clientes con pedido.
    const demanda = (dia && dia.demanda) || {};
    r.clientes.forEach((c) => {
      if (c.nodo === depot.nodo || enRuta.has(c.nodo)) return;
      const t = demanda[String(c.nodo)];
      if (t && dia && dia.estado !== 'resuelto') {
        const icono = L.divIcon({ className: '', iconSize: [14, 14], iconAnchor: [7, 7], html: '<div class="marcador-pendiente"></div>' });
        L.marker([c.lat, c.lon], { icon: icono, keyboard: false })
          .bindTooltip(infoCliente(c, [['Demanda', fmtT(t, 2)], ['Estado', 'Sin ruta asignada']]), { className: 'info', direction: 'auto' })
          .addTo(capaGris);
        return;
      }
      L.circleMarker([c.lat, c.lon], { radius: 5, color: css('--gris-mapa'), weight: 1, fillColor: css('--gris-mapa'), fillOpacity: 0.45, opacity: 0.6 })
        .bindTooltip(infoCliente(c, [['Demanda', 'Sin pedido este día']]), { className: 'info', direction: 'auto' })
        .addTo(capaGris);
    });

    // Depot
    const iconoDepot = L.divIcon({
      className: '', iconSize: [30, 30], iconAnchor: [15, 15],
      html: '<div class="marcador-depot"><svg viewBox="0 0 24 24"><path d="M3 11 12 4l9 7v9H3z M9 20v-6h6v6" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg></div>',
    });
    L.marker([depot.lat, depot.lon], { icon: iconoDepot, zIndexOffset: 1000, keyboard: false })
      .bindTooltip('CD ' + esc(depot.nombre), { permanent: true, direction: 'top', offset: [0, -16], className: 'etiqueta-depot' })
      .addTo(capaDepot);

    aplicarVisibilidad(false);
    dibujarLeyenda(dia);
  }

  function rutaVisible(id) {
    return estado.rutaAislada ? estado.rutaAislada === id : !estado.rutasOcultas.has(id);
  }

  function aplicarVisibilidad(ajustar) {
    Object.entries(capasPorRuta).forEach(([id, capa]) => {
      if (rutaVisible(id)) capaRutas.addLayer(capa.grupo); else capaRutas.removeLayer(capa.grupo);
    });
    dibujarFlechas();
    if (ajustar) reencuadrar();
  }

  // Flechas de sentido en la mitad de cada tramo (se recalculan con el zoom).
  function dibujarFlechas() {
    if (!capaFlechas) return;
    capaFlechas.clearLayers();
    const halo = css('--halo');
    Object.entries(capasPorRuta).forEach(([id, capa]) => {
      if (!rutaVisible(id)) return;
      for (let i = 0; i < capa.puntos.length - 1; i++) {
        const a = mapa.latLngToLayerPoint(capa.puntos[i]);
        const b = mapa.latLngToLayerPoint(capa.puntos[i + 1]);
        const dx = b.x - a.x, dy = b.y - a.y;
        if (Math.hypot(dx, dy) < 46) continue;
        const medio = mapa.layerPointToLatLng(L.point((a.x + b.x) / 2, (a.y + b.y) / 2));
        const angulo = Math.atan2(dy, dx) * 180 / Math.PI;
        const icono = L.divIcon({
          className: 'flecha', iconSize: [14, 14], iconAnchor: [7, 7],
          html: `<svg width="14" height="14" viewBox="0 0 14 14" style="transform:rotate(${angulo}deg)"><path d="M2 2 L12 7 L2 12 L5 7 Z" fill="${capa.color}" stroke="${halo}" stroke-width="1.4" stroke-linejoin="round"/></svg>`,
        });
        L.marker(medio, { icon: icono, interactive: false, keyboard: false }).addTo(capaFlechas);
      }
    });
  }

  function dibujarLeyenda(dia) {
    const div = leyenda.getContainer();
    if (!dia || dia.estado !== 'resuelto' || !dia.rutas.length) {
      const pendientes = dia && (dia.estado === 'infactible' || dia.estado === 'error')
        ? '<div class="fila"><span class="marcador-pendiente" style="width:12px;height:12px"></span>Con pedido, sin ruta</div>' : '';
      div.innerHTML = `<h3>Mapa</h3>${pendientes}<div class="gris"><i></i>Clientes sin pedido</div>`;
      return;
    }
    const filas = dia.rutas.map((r) => {
      const color = colorRuta(r.vehiculo);
      return `<div class="fila">
        <label><input type="checkbox" data-ver="${esc(r.vehiculo)}" ${!estado.rutasOcultas.has(r.vehiculo) ? 'checked' : ''} ${estado.rutaAislada ? 'disabled' : ''}>
          <span class="linea-muestra" style="background:${color}"></span><strong>${esc(r.vehiculo)}</strong>
          <span class="paradas-n">${r.n_paradas} paradas</span></label>
        <button type="button" class="aislar" data-aislar="${esc(r.vehiculo)}" aria-pressed="${estado.rutaAislada === r.vehiculo}">Aislar</button>
      </div>`;
    }).join('');
    div.innerHTML = `<h3><span>Rutas</span><button type="button" class="todas" data-todas>Ver todas</button></h3>${filas}
      <div class="gris"><i></i>Clientes sin pedido</div>`;
    div.querySelectorAll('[data-ver]').forEach((el) => el.addEventListener('change', () => {
      if (el.checked) estado.rutasOcultas.delete(el.dataset.ver); else estado.rutasOcultas.add(el.dataset.ver);
      aplicarVisibilidad(false);
    }));
    div.querySelectorAll('[data-aislar]').forEach((el) => el.addEventListener('click', () => seleccionarRuta(el.dataset.aislar)));
    div.querySelector('[data-todas]').addEventListener('click', () => {
      estado.rutaAislada = null; estado.rutasOcultas.clear();
      renderizarTabla(diaActual()); dibujarLeyenda(diaActual()); aplicarVisibilidad(true);
    });
  }

  function seleccionarRuta(id) {
    estado.rutaAislada = estado.rutaAislada === id ? null : id;
    renderizarTabla(diaActual());
    dibujarLeyenda(diaActual());
    aplicarVisibilidad(true);
  }

  // ---------------------------------------------------------------- Encabezado y pestañas
  function diaActual() {
    if (!estado.resultados || !estado.dia) return null;
    return estado.resultados.dias.find((d) => d.dia === estado.dia) || null;
  }

  function renderizarEncabezado() {
    const r = estado.resultados;
    $('#nombre-depot').textContent = r ? r.depot.nombre : '—';
    $('#meta-archivo').textContent = r ? r.archivo : '—';
    $('#meta-archivo').title = r ? r.archivo : '';
    $('#meta-fecha').textContent = r ? fmtFecha(r.fecha_calculo) : '—';
    $('#meta-dia').textContent = estado.dia || '—';
    $('#meta-base').hidden = !(r && r.usa_datos_base);
    document.title = 'Tablero de rutas' + (estado.dia ? ' · ' + estado.dia : '');
  }

  function renderizarPestanas() {
    const r = estado.resultados;
    const cont = $('#pestanas');
    if (!r) { cont.innerHTML = ''; return; }
    cont.innerHTML = r.dias.map((d) => {
      const sel = d.dia === estado.dia;
      let extra = '', deshabilitada = false, titulo = '';
      if (d.estado === 'sin_datos') { extra = '<span class="sub">Sin datos</span>'; deshabilitada = true; titulo = 'La columna del día está vacía'; }
      else if (d.estado === 'pendiente') { extra = '<span class="girando" aria-label="calculando"></span>'; deshabilitada = true; titulo = 'Calculando…'; }
      else if (d.estado === 'infactible') { extra = '<span class="ico-alerta" aria-label="infactible">⚠</span>'; titulo = 'Día infactible: ver diagnóstico'; }
      else if (d.estado === 'error') { extra = '<span class="ico-alerta" aria-label="error">⚠</span>'; titulo = 'Error al calcular'; }
      return `<button type="button" role="tab" class="pestana" aria-selected="${sel}" data-dia="${esc(d.dia)}" ${deshabilitada ? 'disabled' : ''} title="${esc(titulo)}">${esc(d.dia)}${extra}</button>`;
    }).join('');
    cont.querySelectorAll('.pestana').forEach((b) => b.addEventListener('click', () => seleccionarDia(b.dataset.dia)));
  }

  function seleccionarDia(dia) {
    estado.dia = dia;
    estado.rutaAislada = null;
    estado.rutasOcultas.clear();
    renderizarPestanas();
    renderizarDia(true);
  }

  function renderizarEstadoCalculo() {
    const cont = $('#estado-calculo');
    const p = estado.progreso;
    const d = diaActual();
    let html = '';
    if (p && p.estado === 'en_curso') {
      const nombres = p.en_curso.length ? p.en_curso.join(', ') : '…';
      const pct = p.total ? (100 * p.hechos / p.total) : 0;
      html += `<span class="progreso"><span class="girando"></span>Resolviendo ${esc(nombres)} · ${p.hechos} de ${p.total} listos
        <span class="barra-progreso" aria-hidden="true"><i style="width:${pct}%"></i></span></span>`;
    }
    if (d && d.estado === 'resuelto') {
      const i = d.indicadores;
      const completo = i.clientes_atendidos === i.clientes_con_demanda;
      const verif = d.verificacion_ok;
      html += `<span class="confirmacion" title="${esc((d.verificacion || []).map((v) => (v.cumple ? '✔ ' : '✘ ') + v.criterio).join('\n'))}">
        <span class="${completo ? 'ok' : 'mal'}">${completo ? '✓' : '✗'}</span>Clientes atendidos ${i.clientes_atendidos}/${i.clientes_con_demanda}
        · <span class="${verif ? 'ok' : 'mal'}">${verif ? '✓' : '✗'}</span>Restricciones verificadas</span>`;
    }
    cont.innerHTML = html;
  }

  // ---------------------------------------------------------------- Indicadores
  function tarjeta(titulo, valor, detalle, clase) {
    return `<article class="kpi ${clase || ''}"><div class="kpi-titulo">${titulo}</div><div class="kpi-valor">${valor}</div><div class="kpi-detalle">${detalle}</div></article>`;
  }

  // El semáforo compara el porcentaje tal como se muestra (redondeado), para que texto y color coincidan.
  function semaforo(pct, tipo) {
    const p = estado.resultados.parametros;
    pct = Math.round(pct);
    if (pct >= p.umbral_alerta_pct) return `<span class="sem alerta" title="Alerta: ${tipo} ≥ ${fmtNum(p.umbral_alerta_pct)} %">▲ ${fmtNum(pct)} %</span>`;
    if (tipo === 'capacidad' && pct < p.umbral_subutilizado_pct) return `<span class="sem bajo" title="Vehículo subutilizado: menos de ${fmtNum(p.umbral_subutilizado_pct)} % de capacidad">▼ ${fmtNum(pct)} %</span>`;
    return `<span class="sem ok" title="Normal">● ${fmtNum(pct)} %</span>`;
  }
  function claseBarra(pct, tipo) {
    const p = estado.resultados.parametros;
    pct = Math.round(pct);
    if (pct >= p.umbral_alerta_pct) return 'alerta';
    if (tipo === 'capacidad' && pct < p.umbral_subutilizado_pct) return 'bajo';
    return '';
  }

  function renderizarKpis(d) {
    const cont = $('#kpis');
    if (!d) { cont.innerHTML = ''; return; }
    if (d.estado === 'infactible' || d.estado === 'error') {
      const g = d.diagnostico || { titulo: 'No fue posible calcular el día', mensaje: d.mensaje || '' };
      cont.innerHTML = `<div class="diagnostico" role="alert"><span class="ico" aria-hidden="true">⚠</span><div>
        <h3>${esc(d.dia)}: ${esc(g.titulo)}</h3><p>${esc(g.mensaje)}</p></div></div>`;
      return;
    }
    if (d.estado === 'pendiente') {
      cont.innerHTML = `<div class="diagnostico neutro"><span class="ico"><span class="girando"></span></span><div><h3>Calculando ${esc(d.dia)}…</h3><p>El día aparecerá apenas termine.</p></div></div>`;
      return;
    }
    if (d.estado !== 'resuelto') { cont.innerHTML = ''; return; }
    const i = d.indicadores;
    const pr = estado.resultados.parametros;
    const porTipo = i.por_tipo.map((t) => `${esc(t.codigo)} ${t.usados}/${t.disponibles}`).join(' · ');
    const holgura = Math.floor(i.holgura_min);
    const alertaJornada = Math.round(100 - 100 * i.holgura_min / pr.jornada_max_min) >= pr.umbral_alerta_pct;
    cont.innerHTML = [
      tarjeta('Costo total del plan', fmtCOP(i.costo_total), `${fmtCOP(i.costo_por_t)} por tonelada · ${fmtT(i.toneladas)}`),
      tarjeta('Distancia total', `${fmtNum(i.km_total, 1)}<span class="unidad">km</span>`, `${fmtCOP(i.costo_por_km)} por km en promedio`),
      tarjeta('Vehículos usados', `${i.vehiculos_usados}<span class="de"> / ${i.vehiculos_disponibles}</span>`, porTipo),
      tarjeta('Utilización de capacidad', `${fmtNum(i.utilizacion_pct, 1)}<span class="unidad">%</span>`,
        `${fmtT(i.toneladas)} de ${fmtT(i.capacidad_usada_t)} en vehículos usados`),
      tarjeta('Regreso más tardío', `${esc(i.regreso_max)}<span class="unidad">${esc(i.vehiculo_regreso_max)}</span>`,
        `<span class="sem ${alertaJornada ? 'alerta' : 'ok'}">${alertaJornada ? '▲' : '●'} ${fmtNum(holgura)} min</span> de holgura vs. ${esc(i.hora_limite)}`),
    ].join('');
  }

  // ---------------------------------------------------------------- Tabla de rutas
  function renderizarTabla(d) {
    const cont = $('#contenedor-tabla');
    const pie = $('#sin-despachar');
    $('#ayuda-rutas').hidden = !(d && d.estado === 'resuelto');
    if (!d || d.estado !== 'resuelto') {
      pie.hidden = true;
      if (d && (d.estado === 'infactible' || d.estado === 'error')) {
        const previo = estado.resultados.resumen_previo || {};
        cont.innerHTML = `<div class="vacio-panel"><h3>No se generan rutas para ${esc(d.dia)}</h3>
          <p>El plan no se publica porque no cumple todas las restricciones. Datos del día:</p>
          <ul><li>Demanda: <strong>${fmtT(d.demanda_total_t || 0)}</strong> en ${d.clientes_con_pedido || 0} clientes</li>
          <li>Capacidad de la flota: <strong>${fmtT(previo.capacidad_flota_t || 0)}</strong> en ${previo.vehiculos_disponibles || 0} vehículos</li>
          <li>Jornada máxima: <strong>${fmtNum(estado.resultados.parametros.jornada_max_min)} min</strong> por vehículo</li></ul>
          <p>Opciones: reprogramar pedidos a otro día, agregar vehículos o ajustar la jornada en la hoja Vehiculos / configuración.</p></div>`;
      } else if (d && d.estado === 'pendiente') {
        cont.innerHTML = `<div class="vacio-panel"><span class="girando"></span> Calculando ${esc(d.dia)}…</div>`;
      } else {
        cont.innerHTML = '<div class="vacio-panel">No hay rutas para mostrar.</div>';
      }
      return;
    }
    const filas = d.rutas.map((r) => {
      const color = colorRuta(r.vehiculo);
      const activa = estado.rutaAislada === r.vehiculo;
      let html = `<tr class="fila-ruta ${activa ? 'activa' : ''}" data-vehiculo="${esc(r.vehiculo)}" tabindex="0" aria-expanded="${activa}">
        <td><span class="vehiculo"><span class="muestra" style="background:${color}"></span>${esc(r.vehiculo)} <span class="tipo">Tipo ${esc(r.codigo)}</span></span>
          <span class="vehiculo-sub">${r.n_paradas} paradas</span></td>
        <td class="medidor"><div class="linea"><span class="val">${fmtNum(r.carga_t, 1)} / ${fmtNum(r.capacidad_t, r.capacidad_t % 1 ? 1 : 0)} t</span>${semaforo(r.pct_capacidad, 'capacidad')}</div>
          <div class="barra ${claseBarra(r.pct_capacidad, 'capacidad')}"><i style="width:${Math.min(100, r.pct_capacidad)}%"></i></div></td>
        <td class="medidor"><div class="linea"><span class="val">${fmtDur(r.duracion_min)}</span>${semaforo(r.pct_jornada, 'jornada')}</div>
          <div class="barra ${claseBarra(r.pct_jornada, 'jornada')}"><i style="width:${Math.min(100, r.pct_jornada)}%"></i></div></td>
        <td class="n">${fmtNum(r.km, 1)}</td>
        <td class="n">${esc(r.hora_regreso)}</td>
        <td class="n">${fmtCOP(r.costo)}</td>
      </tr>`;
      if (activa) html += `<tr class="detalle"><td colspan="6">${tablaParadas(r, color)}</td></tr>`;
      return html;
    }).join('');
    cont.innerHTML = `<table class="rutas"><thead><tr>
        <th>Vehículo · paradas</th><th>Carga / capacidad</th><th>Jornada</th><th class="n">Km</th><th class="n">Regreso</th><th class="n">Costo</th>
      </tr></thead><tbody>${filas}</tbody></table>`;
    cont.querySelectorAll('tr.fila-ruta').forEach((tr) => {
      tr.addEventListener('click', () => seleccionarRuta(tr.dataset.vehiculo));
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); seleccionarRuta(tr.dataset.vehiculo); } });
    });
    const activa = cont.querySelector('tr.fila-ruta.activa');
    if (activa) activa.scrollIntoView({ block: 'nearest' });
    pie.hidden = false;
    pie.innerHTML = d.sin_despachar.length
      ? `Sin despachar: <strong>${d.sin_despachar.map(esc).join(', ')}</strong>`
      : 'Sin despachar: <strong>ninguno</strong>';
  }

  function tablaParadas(r, color) {
    const salida = estado.resultados.parametros.inicio_jornada;
    const filas = r.paradas.map((p) => `<tr>
        <td><span class="num-parada" style="background:${color};color:${textoSobre(color)}">${p.orden}</span></td>
        <td class="cliente">${esc(p.cliente)}</td><td class="tabular">${esc(p.eta)}</td>
        <td class="tabular">${fmtNum(p.entrega_t, 1)} t</td><td class="tabular">${fmtNum(p.carga_restante_t, 1)} t</td></tr>`).join('');
    return `<table class="paradas"><thead><tr><th>#</th><th>Cliente</th><th>ETA</th><th>Entrega</th><th>Carga restante</th></tr></thead><tbody>
      <tr class="extremo"><td></td><td>Salida del depot</td><td class="tabular">${esc(salida)}</td><td></td><td class="tabular">${fmtNum(r.carga_t, 1)} t</td></tr>
      ${filas}
      <tr class="extremo"><td></td><td>Regreso al depot</td><td class="tabular">${esc(r.hora_regreso)}</td><td></td><td class="tabular">0 t</td></tr>
    </tbody></table>`;
  }

  // ---------------------------------------------------------------- Comparativo semanal
  let grafico = null;
  const etiquetasToneladas = {
    id: 'etiquetasToneladas',
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      const meta = chart.getDatasetMeta(0);
      const extra = chart.$toneladas || [];
      ctx.save();
      ctx.font = '600 12.5px "Segoe UI", system-ui, sans-serif';
      ctx.fillStyle = css('--texto-2');
      ctx.textAlign = 'center';
      ctx.textBaseline = 'bottom';
      meta.data.forEach((barra, i) => ctx.fillText(fmtT(extra[i] || 0), barra.x, barra.y - 3));
      ctx.restore();
    },
  };

  function actualizarGrafico() {
    const lienzo = $('#grafico');
    if (!estado.resultados || typeof Chart === 'undefined') return;
    const resueltos = estado.resultados.dias.filter((d) => d.estado === 'resuelto');
    const etiquetas = resueltos.map((d) => d.dia);
    const costos = resueltos.map((d) => Math.round(d.indicadores.costo_total));
    const toneladas = resueltos.map((d) => d.indicadores.toneladas);
    const acento = css('--acento');
    const colores = resueltos.map((d) => d.dia === estado.dia ? acento : colorMezcla(acento, 0.42));
    const datos = { labels: etiquetas, datasets: [{ data: costos, backgroundColor: colores, borderRadius: 5, maxBarThickness: 46 }] };
    const maximo = Math.max(1, ...costos);
    const opciones = {
      responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
      layout: { padding: { top: 18 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (c) => `${fmtCOP(c.raw)} · ${fmtT(toneladas[c.dataIndex])} · ${fmtCOP(c.raw / (toneladas[c.dataIndex] || 1))}/t`,
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: css('--texto-2'), font: { size: 12.5, weight: '600' } }, border: { color: css('--borde') } },
        y: { display: false, beginAtZero: true, suggestedMax: maximo * 1.12 },
      },
      onClick: (_e, elementos) => { if (elementos.length) seleccionarDia(etiquetas[elementos[0].index]); },
      onHover: (e, elementos) => { e.native.target.style.cursor = elementos.length ? 'pointer' : 'default'; },
    };
    if (!grafico) {
      grafico = new Chart(lienzo, { type: 'bar', data: datos, options: opciones, plugins: [etiquetasToneladas] });
    } else {
      grafico.data = datos; grafico.options = opciones;
    }
    grafico.$toneladas = toneladas;
    grafico.update();
  }
  function colorMezcla(color, alfa) {
    const c = color.startsWith('#') ? color : '#888888';
    const n = parseInt(c.slice(1), 16);
    return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alfa})`;
  }

  // ---------------------------------------------------------------- Render principal
  function renderizarDia(forzar) {
    const d = diaActual();
    renderizarEncabezado();
    renderizarEstadoCalculo();
    const clave = (d ? d.dia + ':' + d.estado : '') + ':' + tema() + ':' + (estado.resultados ? estado.resultados.fecha_calculo + estado.resultados.archivo : '');
    if (!forzar && clave === estado.claveRender) { actualizarGrafico(); return; }
    estado.claveRender = clave;
    renderizarKpis(d);
    renderizarTabla(d);
    if (mapa && estado.resultados) {
      dibujarMapa(d);
      reencuadrar();
    }
    actualizarGrafico();
  }

  function primerDiaResuelto(r) {
    const d = r.dias.find((x) => x.estado === 'resuelto') || r.dias.find((x) => x.estado === 'infactible' || x.estado === 'error')
      || r.dias.find((x) => x.estado === 'pendiente');
    return d ? d.dia : null;
  }

  function mostrarResultados(r, opciones) {
    opciones = opciones || {};
    estado.resultados = r;
    const actual = diaActual();
    const seleccionable = actual && actual.estado !== 'pendiente' && actual.estado !== 'sin_datos';
    if (opciones.reiniciar || !seleccionable) {
      const primero = primerDiaResuelto(r);
      if (primero !== estado.dia) { estado.dia = primero; estado.rutaAislada = null; estado.rutasOcultas.clear(); }
    }
    renderizarPestanas();
    renderizarDia(false);
  }

  // ---------------------------------------------------------------- Trabajos y sondeo
  function seguirTrabajo(respuesta, alTerminar) {
    clearTimeout(estado.sondeo);
    estado.trabajo = respuesta.trabajo;
    estado.progreso = respuesta;
    let primeraVez = true;
    const aplicar = (resp) => {
      estado.progreso = resp;
      mostrarResultados(resp.resultados, { reiniciar: primeraVez });
      primeraVez = false;
      actualizarPanelCarga(resp);
      if (resp.estado === 'terminado') {
        estado.trabajo = null;
        estado.progreso = null;
        const primero = primerDiaResuelto(resp.resultados);
        if (primero) seleccionarDia(primero);
        renderizarEstadoCalculo();
        if (alTerminar) alTerminar(resp.resultados);
        return;
      }
      estado.sondeo = setTimeout(consultar, 700);
    };
    const consultar = () => {
      fetch(API.trabajo(estado.trabajo)).then(leerJson).then(aplicar).catch((e) => {
        avisar(e.message || 'Se perdió la conexión con el servidor.', true);
        estado.progreso = null; renderizarEstadoCalculo();
      });
    };
    aplicar(respuesta);
  }

  async function leerJson(resp) {
    let cuerpo = null;
    try { cuerpo = await resp.json(); } catch (e) { /* respuesta no JSON */ }
    if (!resp.ok) throw new Error((cuerpo && cuerpo.mensaje) || 'El servidor no respondió correctamente.');
    return cuerpo;
  }

  function cargarInicial() {
    fetch(API.inicial).then(leerJson).then((resp) => {
      if (!resp || !resp.resultados) throw new Error('No hay resultados del archivo de ejemplo.');
      if (resp.trabajo && resp.estado === 'en_curso') seguirTrabajo(resp);
      else mostrarResultados(resp.resultados, { reiniciar: true });
    }).catch((e) => avisar(e.message || 'No fue posible cargar el archivo de ejemplo.', true));
  }

  // ---------------------------------------------------------------- Carga de archivos
  function validarEnCliente(archivo) {
    if (!/\.xlsx$/i.test(archivo.name)) return `«${archivo.name}» no es un libro de Excel .xlsx. Solo se aceptan archivos .xlsx.`;
    if (archivo.size > TAMANO_MAX_MB * 1024 * 1024) return `El archivo pesa ${fmtNum(archivo.size / 1048576, 1)} MB; el máximo es ${TAMANO_MAX_MB} MB.`;
    if (archivo.size === 0) return 'El archivo está vacío.';
    return null;
  }

  function cargarArchivo(archivo) {
    if (!archivo) return;
    if (estado.trabajo && estado.progreso && estado.progreso.estado === 'en_curso') {
      avisar('Espere a que termine el cálculo en curso antes de cargar otro archivo.', true);
      return;
    }
    const error = validarEnCliente(archivo);
    if (error) { mostrarErrores(archivo.name, [{ hoja: '', columna: '', nodo: '', problema: error }]); return; }
    const datos = new FormData();
    datos.append('archivo', archivo);
    avisar('Validando «' + archivo.name + '»…');
    $('#btn-cargar').disabled = true;
    fetch(API.cargar, { method: 'POST', body: datos }).then(leerJson).then((resp) => {
      ocultarAviso();
      if (!resp.ok) { mostrarErrores(resp.archivo, resp.errores); return; }
      abrirPanelCarga(resp);
      seguirTrabajo(resp, (r) => {
        avisar('Cálculo terminado: ' + r.mensaje_final);
        setTimeout(() => { if (!estado.trabajo) cerrarPanelCarga(); }, 9000);
      });
    }).catch((e) => avisar(e.message || 'No fue posible enviar el archivo.', true))
      .finally(() => { $('#btn-cargar').disabled = false; $('#entrada-archivo').value = ''; });
  }

  function mostrarErrores(nombre, errores) {
    $('#errores-intro').innerHTML = `Se encontraron <strong>${errores.length}</strong> problema(s) en «${esc(nombre)}». No se ejecutó el cálculo.`;
    const filas = errores.map((e) => `<tr><td>${esc(e.hoja) || '—'}</td><td>${esc(e.columna) || '—'}</td><td>${esc(e.nodo) || '—'}</td><td>${esc(e.problema)}</td></tr>`).join('');
    $('#errores-tabla').innerHTML = `<table><thead><tr><th>Hoja</th><th>Columna</th><th>Nodo</th><th>Problema</th></tr></thead><tbody>${filas}</tbody></table>`;
    const dlg = $('#dialogo-errores');
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  }

  function abrirPanelCarga(resp) {
    estado.panelCargaAbierto = true;
    $('#panel-carga').hidden = false;
    actualizarPanelCarga(resp);
  }
  function cerrarPanelCarga() { estado.panelCargaAbierto = false; $('#panel-carga').hidden = true; }

  function actualizarPanelCarga(resp) {
    if (!estado.panelCargaAbierto) return;
    const r = resp.resultados;
    const previo = r.resumen_previo;
    const estados = {};
    r.dias.forEach((d) => { estados[d.dia] = d.estado; });
    const etiquetaEstado = (e) => ({
      resuelto: '<span class="estado-dia resuelto">✓ Resuelto</span>',
      infactible: '<span class="estado-dia infactible">⚠ Infactible</span>',
      error: '<span class="estado-dia error">⚠ Error</span>',
      sin_datos: '<span class="estado-dia sin_datos">Sin datos</span>',
      pendiente: '<span class="estado-dia"><span class="girando"></span> Calculando</span>',
    }[e] || e);
    const filas = previo.dias.map((d) => `<tr><td>${esc(d.dia)}</td>
      <td class="n">${d.con_datos ? d.clientes : '—'}</td><td class="n">${d.con_datos ? fmtNum(d.toneladas, 1) : '—'}</td>
      <td class="n">${d.con_datos ? fmtNum(d.uso_pct, 0) + ' %' : '—'}</td><td>${etiquetaEstado(estados[d.dia])}</td></tr>`).join('');
    const avisos = [];
    if (r.usa_datos_base) avisos.push('<li>El libro solo trae la hoja Demanda: se usaron los clientes, la matriz y la flota del archivo base.</li>');
    previo.dias.filter((d) => d.excede_capacidad).forEach((d) => avisos.push(
      `<li class="fuerte">⚠ ${esc(d.dia)}: ${fmtT(d.toneladas)} supera la capacidad de la flota (${fmtT(previo.capacidad_flota_t)}).</li>`));
    const sinDatos = previo.dias.filter((d) => !d.con_datos).map((d) => d.dia);
    if (sinDatos.length) avisos.push(`<li>Días sin datos (se omiten): ${sinDatos.map(esc).join(', ')}.</li>`);
    const flota = previo.flota.map((t) => `${t.cantidad}× ${esc(t.codigo)} (${fmtNum(t.capacidad_t, t.capacidad_t % 1 ? 1 : 0)} t)`).join(' · ');
    const progreso = resp.estado === 'en_curso'
      ? `<p class="cap"><span class="girando"></span> Resolviendo ${esc(resp.en_curso.join(', ') || '…')} · ${resp.hechos} de ${resp.total}</p>`
      : `<p class="cap"><strong>${esc(r.mensaje_final || '')}</strong></p>`;
    $('#carga-titulo').textContent = resp.estado === 'en_curso' ? 'Calculando rutas…' : 'Cálculo terminado';
    $('#carga-cuerpo').innerHTML = `<p class="archivo">${esc(r.archivo)}</p>
      <table><thead><tr><th>Día</th><th class="n">Clientes</th><th class="n">Toneladas</th><th class="n">% flota</th><th>Estado</th></tr></thead><tbody>${filas}</tbody></table>
      <p class="cap">Capacidad de la flota: <strong>${fmtT(previo.capacidad_flota_t)}</strong> · ${flota}</p>
      ${avisos.length ? `<ul class="avisos">${avisos.join('')}</ul>` : ''}${progreso}`;
  }

  let temporizadorAviso = null;
  function avisar(texto, error) {
    const el = $('#aviso');
    el.textContent = texto;
    el.className = 'aviso' + (error ? ' error' : '');
    el.hidden = false;
    clearTimeout(temporizadorAviso);
    temporizadorAviso = setTimeout(ocultarAviso, error ? 8000 : 6000);
  }
  function ocultarAviso() { $('#aviso').hidden = true; }

  // ---------------------------------------------------------------- Descargas
  function descargarBlob(blob, nombre) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = nombre;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  }
  async function descargarDesdeServidor(url, opciones, nombre) {
    try {
      const resp = await fetch(url, opciones);
      if (!resp.ok) { await leerJson(resp); return; }
      descargarBlob(await resp.blob(), nombre);
    } catch (e) { avisar(e.message || 'No fue posible generar el archivo.', true); }
  }
  function exigirResultados() {
    if (!estado.resultados) { avisar('Aún no hay resultados.', true); return false; }
    if (estado.progreso && estado.progreso.estado === 'en_curso') { avisar('Espere a que termine el cálculo.', true); return false; }
    return true;
  }
  const cuerpoJson = () => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(estado.resultados) });

  // ---------------------------------------------------------------- Eventos
  function iniciarEventos() {
    $('#btn-cargar').addEventListener('click', () => $('#entrada-archivo').click());
    $('#entrada-archivo').addEventListener('change', (e) => cargarArchivo(e.target.files[0]));
    $('#btn-plantilla').addEventListener('click', () => descargarDesdeServidor(API.plantilla, {}, 'plantilla_demanda.xlsx'));
    $('#btn-rutas').addEventListener('click', () => { if (exigirResultados()) descargarDesdeServidor(API.rutas, cuerpoJson(), 'rutas.xlsx'); });
    $('#btn-exportar').addEventListener('click', () => {
      if (!exigirResultados()) return;
      const base = (estado.resultados.archivo || 'tablero').replace(/\.xlsx$/i, '').replace(/[^\w\-]+/g, '_');
      descargarDesdeServidor(API.exportar, cuerpoJson(), 'tablero_rutas_' + base + '.html');
    });
    $('#cerrar-errores').addEventListener('click', () => $('#dialogo-errores').close());
    $('#cerrar-carga').addEventListener('click', cerrarPanelCarga);

    // Arrastrar y soltar en toda la ventana.
    let profundidad = 0;
    const zona = $('#zona-soltar');
    const conArchivos = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files');
    window.addEventListener('dragenter', (e) => { if (!conArchivos(e)) return; e.preventDefault(); profundidad++; zona.hidden = false; });
    window.addEventListener('dragover', (e) => { if (conArchivos(e)) e.preventDefault(); });
    window.addEventListener('dragleave', (e) => { if (!conArchivos(e)) return; profundidad = Math.max(0, profundidad - 1); if (!profundidad) zona.hidden = true; });
    window.addEventListener('drop', (e) => {
      if (!conArchivos(e)) return;
      e.preventDefault(); profundidad = 0; zona.hidden = true;
      cargarArchivo(e.dataTransfer.files[0]);
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && estado.rutaAislada) seleccionarRuta(estado.rutaAislada);
    });
  }

  function modoExportado() {
    ['#btn-cargar', '#btn-plantilla', '#btn-rutas', '#btn-exportar'].forEach((s) => { $(s).hidden = true; });
    $('#meta-exportado').hidden = false;
    $('.grupo-apariencia').style.borderLeft = '0';
    mostrarResultados(EXPORTADO, { reiniciar: true });
  }

  // ---------------------------------------------------------------- Inicio
  document.addEventListener('DOMContentLoaded', () => {
    iniciarApariencia();
    if (typeof L !== 'undefined') iniciarMapa();
    else $('#mapa').innerHTML = '<div class="vacio-panel">No se pudo cargar el componente de mapa.</div>';
    if (EXPORTADO) { modoExportado(); return; }
    iniciarEventos();
    cargarInicial();
  });
})();
