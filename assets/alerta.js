/* Alerta de convocatorias · Estado X
   Lee data/estado.json y data/cambios.json y pinta el tablero, con buscador y filtros.
   Los textos vienen de páginas de terceros: siempre textContent, nunca innerHTML.
   Los enlaces de los datos solo se usan si empiezan por https://. */
(function () {
  'use strict';

  var F = window.AlertaFiltros;
  var MAX_LINEAS = 5;      // líneas nuevas que se muestran por cambio
  var MAX_CAMBIOS = 10;    // cambios visibles antes de «Ver los anteriores»
  // Carpeta de los datos y lista de fuentes: la lista principal usa data/ y fuentes.txt;
  // una lista aparte los indica en <body data-datos="…" data-fuentes="…">.
  var DATOS = document.body.getAttribute('data-datos') || 'data/';
  var FUENTES = document.body.getAttribute('data-fuentes') || 'fuentes.txt';

  var estadoPagina = { cambios: [], tiposPorNombre: {}, consulta: '', tipo: '', todos: false };

  function $(id) { return document.getElementById(id); }

  function el(etiqueta, clase, texto) {
    var nodo = document.createElement(etiqueta);
    if (clase) nodo.className = clase;
    if (texto !== undefined && texto !== null) nodo.textContent = String(texto);
    return nodo;
  }

  function esSeguro(url) { return typeof url === 'string' && url.indexOf('https://') === 0; }

  function enlace(url, texto, clase, textoOculto) {
    if (!esSeguro(url)) return null;
    var a = el('a', clase, texto);
    a.href = url;
    a.target = '_blank';
    a.rel = 'noopener';
    if (textoOculto) a.appendChild(el('span', 'sr', ' ' + textoOculto));
    return a;
  }

  /* «6:12 a. m.» no se parte entre dos renglones. */
  function fecha(texto) {
    return String(texto).replace(/(\d{1,2}:\d{2}) ([ap])\. m\./g, '$1 $2. m.');
  }

  function plural(n, uno, varios) { return n + ' ' + (n === 1 ? uno : varios); }

  function vaciar(nodo) { while (nodo.firstChild) nodo.removeChild(nodo.firstChild); }

  function listo(nodo) { nodo.setAttribute('aria-busy', 'false'); }

  function pedir(ruta, comoTexto) {
    return fetch(ruta, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(ruta + ': ' + r.status);
      return comoTexto ? r.text() : r.json();
    });
  }

  /* Texto con lo buscado resaltado (en color, sin cursiva). */
  function conResaltado(etiqueta, clase, texto, consulta) {
    var nodo = el(etiqueta, clase);
    F.tramos(texto, consulta).forEach(function (t) {
      nodo.appendChild(t.marca ? el('mark', null, t.texto) : document.createTextNode(t.texto));
    });
    return nodo;
  }

  /* ---------- Las páginas que vigilamos ---------- */

  var ESTADOS = {
    ok: { clase: 'estado--ok', texto: 'Al día' },
    nueva: { clase: 'estado--nueva', texto: 'Recién agregada' },
    error: { clase: 'estado--error', texto: 'No se pudo revisar hoy; se vuelve a intentar mañana' }
  };

  function filaFuente(f) {
    var li = el('li', 'fuente');
    var nombre = f.nombre || 'Página sin nombre';
    li.appendChild(el('h3', 'fuente__nombre', nombre));

    var revisada = f.ultima_revision_texto
      ? 'Revisada ' + fecha(f.ultima_revision_texto)
      : 'Todavía sin revisar';
    var datos = el('div', 'fuente__datos');
    datos.appendChild(el('p', 'fuente__dato', revisada));

    var cambio;
    if (f.ultimo_cambio_texto) cambio = 'Último cambio: ' + fecha(f.ultimo_cambio_texto);
    else if (f.vigilada_desde_texto) cambio = 'Sin cambios desde ' + fecha(f.vigilada_desde_texto);
    else cambio = 'Sin cambios registrados';
    datos.appendChild(el('p', 'fuente__dato', cambio));
    var tipos = F.tiposDe(f, estadoPagina.tiposPorNombre);
    if (tipos.length) datos.appendChild(el('p', 'fuente__dato fuente__tipo', tipos.join(' · ')));
    li.appendChild(datos);

    var info = ESTADOS[f.estado] || ESTADOS.ok;
    var estado = el('p', 'estado ' + info.clase);
    var punto = el('span', 'punto');
    punto.setAttribute('aria-hidden', 'true');
    estado.appendChild(punto);
    estado.appendChild(el('span', null, info.texto));
    li.appendChild(estado);

    var a = enlace(f.url, 'Página oficial', 'fuente__enlace', 'de ' + nombre);
    li.appendChild(a || el('span', 'fuente__enlace fuente__enlace--no', 'Sin enlace'));
    return li;
  }

  function pintarFuentes(estado) {
    var caja = $('lista-fuentes');
    var fuentes = Array.isArray(estado.fuentes) ? estado.fuentes : [];
    vaciar(caja);
    if (!fuentes.length) {
      caja.appendChild(el('p', 'aviso', 'Todavía no hay páginas en la lista.'));
    } else {
      var ul = el('ul', 'fuentes');
      fuentes.forEach(function (f) { ul.appendChild(filaFuente(f)); });
      caja.appendChild(ul);
    }
    listo(caja);

    $('ultima-revision').textContent = estado.generado_texto ? fecha(estado.generado_texto) : 'sin dato';
    $('n-fuentes').textContent = String(fuentes.length);
    var conError = fuentes.filter(function (f) { return f.estado === 'error'; }).length;
    $('punto-general').className = 'punto ' + (conError ? 'punto--error' : 'punto--ok');
  }

  /* RSS por tipo: solo los que la revisión ya escribió (vienen en estado.json). */
  function pintarRssPorTipo(estado) {
    var caja = $('rss-tipos');
    if (!caja || !estado || !Array.isArray(estado.tipos) || !estado.tipos.length) return;
    var ul = el('ul', 'rss-tipos__lista');
    var raiz = DATOS.replace(/data\/(.*)$/, '');
    estado.tipos.forEach(function (t) {
      if (!t || typeof t.feed !== 'string' || !/^data\/[a-z0-9\/-]+\.xml$/.test(t.feed)) return;
      var li = el('li');
      var a = el('a', null, t.nombre);
      a.href = raiz + t.feed;
      li.appendChild(a);
      ul.appendChild(li);
    });
    if (!ul.firstChild) return;
    caja.appendChild(ul);
    caja.hidden = false;
  }

  /* ---------- Lo que cambió ---------- */

  function tarjetaCambio(c, consulta) {
    var li = el('li', 'cambio');
    var nombre = c.nombre || 'Página sin nombre';
    var nAgregadas = typeof c.agregadas_n === 'number' ? c.agregadas_n : (c.agregadas || []).length;

    var lineas = F.lineasOrdenadas(c, consulta);
    var ul = el('ul', 'lineas');
    lineas.slice(0, MAX_LINEAS).forEach(function (linea) {
      ul.appendChild(conResaltado('li', null, linea, consulta));
    });
    li.appendChild(ul);
    var resto = nAgregadas - Math.min(lineas.length, MAX_LINEAS);
    if (resto > 0) li.appendChild(el('p', 'lineas__mas', resto === 1 ? 'Y 1 línea más en la página oficial' : 'Y ' + resto + ' líneas más en la página oficial'));

    var pie = el('div', 'cambio__pie');
    var quien = el('div', 'cambio__quien');
    quien.appendChild(conResaltado('h3', 'cambio__nombre', nombre, consulta));
    var datos = [fecha(c.fecha_texto || '')];
    var tipos = F.tiposDe(c, estadoPagina.tiposPorNombre);
    if (tipos.length) datos.push(tipos.join(' · '));
    datos.push(plural(nAgregadas, 'línea nueva', 'líneas nuevas'));
    quien.appendChild(el('p', 'cambio__fecha', datos.join(' · ')));
    pie.appendChild(quien);

    var acciones = el('p', 'cambio__acciones');
    var oficial = enlace(c.url, 'Abrir la página oficial', 'boton boton--chico', 'de ' + nombre);
    if (oficial) acciones.appendChild(oficial);
    var aviso = enlace(c.aviso, 'Ver el aviso en GitHub', 'cambio__aviso', 'de ' + nombre);
    if (aviso) acciones.appendChild(aviso);
    if (acciones.firstChild) pie.appendChild(acciones);
    li.appendChild(pie);
    return li;
  }

  function desdeCuando(estado) {
    var fuentes = (estado && Array.isArray(estado.fuentes)) ? estado.fuentes : [];
    var primera = null;
    fuentes.forEach(function (f) {
      if (!f.vigilada_desde_texto) return;
      if (!primera || String(f.vigilada_desde || '') < String(primera.vigilada_desde || '')) primera = f;
    });
    return primera ? primera.vigilada_desde_texto : null;
  }

  /* Filtros por tipo: un botón por tipo, más «Todo». Solo si hay al menos dos tipos. */
  function pintarFiltros() {
    var caja = $('filtro-tipos');
    var tipos = F.tiposEnOrden(estadoPagina.tiposPorNombre);
    vaciar(caja);
    if (tipos.length < 2) { estadoPagina.tipo = ''; caja.hidden = true; return; }
    if (tipos.indexOf(estadoPagina.tipo) === -1) estadoPagina.tipo = '';
    [''].concat(tipos).forEach(function (t) {
      var b = el('button', 'chip', t || 'Todo');
      b.type = 'button';
      b.setAttribute('aria-pressed', String(estadoPagina.tipo === t));
      b.addEventListener('click', function () {
        estadoPagina.tipo = t;
        Array.prototype.forEach.call(caja.querySelectorAll('.chip'), function (otro) {
          otro.setAttribute('aria-pressed', String(otro === b));
        });
        recordarEnDireccion();
        pintarLista();
      });
      caja.appendChild(b);
    });
    caja.hidden = false;
  }

  function pintarLista() {
    var caja = $('lista-cambios');
    var cambios = estadoPagina.cambios;
    var consulta = estadoPagina.consulta;
    var tipo = estadoPagina.tipo;
    var filtrando = !!(F.terminos(consulta).length || tipo);
    var conNovedades = cambios.filter(function (c) { return !F.sinNovedades(c); });
    var vistos = conNovedades.filter(function (c) {
      return F.coincide(c, consulta, tipo, estadoPagina.tiposPorNombre);
    });
    vaciar(caja);

    var cuenta = $('cuenta-filtro');
    if (filtrando) {
      cuenta.textContent = vistos.length
        ? 'Mostrando ' + plural(vistos.length, 'cambio', 'cambios') + ' de ' + conNovedades.length + '.'
        : 'Ningún cambio con esa búsqueda.';
    } else {
      cuenta.textContent = '';
    }

    if (!vistos.length) {
      var vacio = el('div', 'vacio');
      if (filtrando) {
        vacio.appendChild(el('p', 'vacio__titulo', 'Nada con esa búsqueda.'));
        vacio.appendChild(el('p', 'vacio__texto',
          'Prueba con otra palabra o quita el filtro. Solo buscamos en las líneas nuevas de cada cambio, no en toda la página oficial.'));
        var limpiar = el('button', 'boton boton--linea', 'Ver todo');
        limpiar.type = 'button';
        limpiar.addEventListener('click', function () { limpiarFiltros(); });
        vacio.appendChild(limpiar);
      } else {
        var desde = desdeCuando(estadoPagina.estado);
        vacio.appendChild(el('p', 'vacio__titulo',
          desde ? 'Todavía no hay novedades: la vigilancia empezó el ' + fecha(desde).replace(/\.$/, '') + '.'
                : 'Todavía no hay novedades.'));
        vacio.appendChild(el('p', 'vacio__texto',
          'Cuando aparezca algo nuevo en una de las páginas, aquí verás cuál fue y qué líneas aparecieron.'));
      }
      caja.appendChild(vacio);
    } else {
      var ol = el('ol', 'cambios');
      vistos.forEach(function (c, i) {
        var item = tarjetaCambio(c, consulta);
        if (!filtrando && !estadoPagina.todos && i >= MAX_CAMBIOS) item.hidden = true;
        ol.appendChild(item);
      });
      caja.appendChild(ol);

      if (!filtrando && !estadoPagina.todos && vistos.length > MAX_CAMBIOS) {
        var boton = el('button', 'boton boton--linea ver-mas',
          'Ver los ' + (vistos.length - MAX_CAMBIOS) + ' anteriores');
        boton.type = 'button';
        boton.addEventListener('click', function () {
          estadoPagina.todos = true;
          Array.prototype.forEach.call(ol.querySelectorAll('li.cambio[hidden]'), function (li) { li.hidden = false; });
          boton.remove();
        });
        caja.appendChild(boton);
      }
    }
    pintarSinNovedades(cambios);
    listo(caja);
  }

  /* Las revisiones en que solo desaparecieron líneas: abajo, plegadas, sin aviso. */
  function pintarSinNovedades(cambios) {
    var caja = $('sin-novedades');
    if (!caja) return;
    var lista = cambios.filter(F.sinNovedades);
    vaciar(caja);
    if (!lista.length) { caja.hidden = true; return; }
    var det = el('details', 'sin-novedades');
    det.appendChild(el('summary', null, plural(lista.length, 'revisión', 'revisiones') + ' sin novedades'));
    det.appendChild(el('p', 'nota', 'En estas solo desaparecieron líneas de la página: no apareció nada nuevo, así que no hubo aviso.'));
    var ul = el('ul', 'sin-novedades__lista');
    lista.forEach(function (c) {
      ul.appendChild(el('li', null, (c.nombre || 'Página sin nombre') + ' · ' + fecha(c.fecha_texto || '')));
    });
    det.appendChild(ul);
    caja.appendChild(det);
    caja.hidden = false;
  }

  function pintarResumen() {
    var n = F.resumenSemana(estadoPagina.cambios, new Date());
    $('resumen-semana').textContent = n
      ? 'Esta semana: ' + plural(n, 'página con algo nuevo', 'páginas con algo nuevo') + '.'
      : 'Esta semana todavía no aparece nada nuevo.';
  }

  function limpiarFiltros() {
    estadoPagina.consulta = '';
    estadoPagina.tipo = '';
    $('buscar').value = '';
    pintarFiltros();
    recordarEnDireccion();
    pintarLista();
  }

  /* La búsqueda y el filtro quedan en la dirección (?buscar=beca&tipo=Becas) para compartirlos. */
  function recordarEnDireccion() {
    if (!window.history || !window.URLSearchParams) return;
    var p = new URLSearchParams(window.location.search);
    if (estadoPagina.consulta.trim()) p.set('buscar', estadoPagina.consulta.trim()); else p.delete('buscar');
    if (estadoPagina.tipo) p.set('tipo', estadoPagina.tipo); else p.delete('tipo');
    var q = p.toString();
    try {
      window.history.replaceState(null, '', window.location.pathname + (q ? '?' + q : '') + window.location.hash);
    } catch (e) { /* sin historial (archivo local): no pasa nada */ }
  }

  function leerDireccion() {
    if (!window.URLSearchParams) return;
    var p = new URLSearchParams(window.location.search);
    estadoPagina.consulta = (p.get('buscar') || '').slice(0, 80);
    estadoPagina.tipo = (p.get('tipo') || '').slice(0, 40);
    $('buscar').value = estadoPagina.consulta;
  }

  function prepararBuscador() {
    var campo = $('buscar');
    var espera;
    campo.addEventListener('input', function () {
      clearTimeout(espera);
      espera = setTimeout(function () {
        estadoPagina.consulta = campo.value.slice(0, 80);
        recordarEnDireccion();
        pintarLista();
      }, 120);
    });
    $('form-buscar').addEventListener('submit', function (e) { e.preventDefault(); });
  }

  function fallo(idCaja, texto) {
    var caja = $(idCaja);
    vaciar(caja);
    caja.appendChild(el('p', 'aviso aviso--error', texto));
    listo(caja);
  }

  /* ---------- Arranque ---------- */

  leerDireccion();
  prepararBuscador();

  var estadoListo = pedir(DATOS + 'estado.json').then(function (estado) {
    estadoPagina.estado = estado;
    (Array.isArray(estado.fuentes) ? estado.fuentes : []).forEach(function (f) {
      if (Array.isArray(f.tipos) && f.tipos.length) estadoPagina.tiposPorNombre[f.nombre] = f.tipos;
    });
    pintarRssPorTipo(estado);
    return estado;
  }).catch(function () {
    return null;
  });

  // Si estado.json todavía no trae los tipos (antes de la primera revisión con tipos),
  // se leen de la lista de fuentes.
  var tiposListos = estadoListo.then(function (estado) {
    if (Object.keys(estadoPagina.tiposPorNombre).length) return estado;
    return pedir(FUENTES, true).then(function (texto) {
      estadoPagina.tiposPorNombre = F.tiposDesdeFuentes(texto);
      return estado;
    }).catch(function () { return estado; });
  });

  tiposListos.then(function (estado) {
    if (estado) {
      pintarFuentes(estado);
    } else {
      fallo('lista-fuentes', 'No pudimos cargar el estado de las páginas. Recarga en un momento; si sigue igual, los avisos siguen llegando por el RSS y por GitHub.');
      $('ultima-revision').textContent = 'no disponible';
      $('n-fuentes').textContent = '–';
    }
    pintarFiltros();
    return pedir(DATOS + 'cambios.json').then(function (datos) {
      estadoPagina.cambios = (datos && Array.isArray(datos.cambios)) ? datos.cambios : [];
      $('n-cambios').textContent = String(estadoPagina.cambios.filter(function (c) { return !F.sinNovedades(c); }).length);
      pintarResumen();
      pintarLista();
    }).catch(function () {
      fallo('lista-cambios', 'No pudimos cargar la lista de cambios. Recarga en un momento; si sigue igual, los avisos siguen llegando por el RSS y por GitHub.');
      $('n-cambios').textContent = '–';
    });
  });
})();
