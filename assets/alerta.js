/* Alerta de convocatorias · Estado X
   Lee data/estado.json y data/cambios.json y pinta el tablero.
   Los textos vienen de páginas de terceros: siempre textContent, nunca innerHTML.
   Los enlaces de los datos solo se usan si empiezan por https://. */
(function () {
  'use strict';

  var MAX_LINEAS = 5;      // líneas nuevas que se muestran por cambio
  var MAX_CAMBIOS = 10;    // cambios visibles antes de «Ver los anteriores»

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
    return String(texto).replace(/(\d{1,2}:\d{2}) ([ap])\. m\./g, '$1 $2. m.');
  }

  function plural(n, uno, varios) { return n + ' ' + (n === 1 ? uno : varios); }

  function vaciar(nodo) { while (nodo.firstChild) nodo.removeChild(nodo.firstChild); }

  function listo(nodo) { nodo.setAttribute('aria-busy', 'false'); }

  function pedir(ruta) {
    return fetch(ruta, { cache: 'no-store' }).then(function (r) {
      if (!r.ok) throw new Error(ruta + ': ' + r.status);
      return r.json();
    });
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

  /* ---------- Lo que cambió ---------- */

  function tarjetaCambio(c) {
    var art = el('li', 'cambio');
    var nombre = c.nombre || 'Página sin nombre';

    var cab = el('div', 'cambio__cab');
    cab.appendChild(el('p', 'cambio__fecha', fecha(c.fecha_texto || '')));
    cab.appendChild(el('h3', 'cambio__nombre', nombre));

    var agregadas = Array.isArray(c.agregadas) ? c.agregadas : [];
    var nAgregadas = typeof c.agregadas_n === 'number' ? c.agregadas_n : agregadas.length;
    var nQuitadas = typeof c.quitadas_n === 'number' ? c.quitadas_n : 0;
    var partes = [];
    if (nAgregadas) partes.push(plural(nAgregadas, 'línea nueva', 'líneas nuevas'));
    if (nQuitadas) partes.push(nQuitadas === 1 ? '1 ya no está' : nQuitadas + ' ya no están');
    if (partes.length) cab.appendChild(el('p', 'cambio__cuenta', partes.join(' · ')));

    var acciones = el('p', 'cambio__acciones');
    var oficial = enlace(c.url, 'Abrir la página oficial', 'boton boton--chico', 'de ' + nombre);
    if (oficial) acciones.appendChild(oficial);
    var aviso = enlace(c.aviso, 'Ver el aviso en GitHub', 'cambio__aviso', 'de ' + nombre);
    if (aviso) acciones.appendChild(aviso);
    if (acciones.firstChild) cab.appendChild(acciones);
    art.appendChild(cab);

    var cuerpo = el('div', 'cambio__cuerpo');
    if (agregadas.length) {
      var ul = el('ul', 'lineas');
      agregadas.slice(0, MAX_LINEAS).forEach(function (linea) {
        ul.appendChild(el('li', null, linea));
      });
      cuerpo.appendChild(ul);
      var resto = nAgregadas - Math.min(agregadas.length, MAX_LINEAS);
      if (resto > 0) cuerpo.appendChild(el('p', 'lineas__mas', '+' + resto + ' más'));
    } else {
      cuerpo.appendChild(el('p', 'lineas__solo', 'Desaparecieron líneas de la página; no apareció texto nuevo.'));
    }
    art.appendChild(cuerpo);
    return art;
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

  function pintarCambios(datos, estado) {
    var caja = $('lista-cambios');
    var cambios = (datos && Array.isArray(datos.cambios)) ? datos.cambios : [];
    vaciar(caja);
    $('n-cambios').textContent = String(cambios.length);

    if (!cambios.length) {
      var vacio = el('div', 'vacio');
      var desde = desdeCuando(estado);
      vacio.appendChild(el('p', 'vacio__titulo',
        desde ? 'Todavía no hay cambios: la vigilancia empezó el ' + fecha(desde).replace(/\.$/, '') + '.'
              : 'Todavía no hay cambios.'));
      vacio.appendChild(el('p', 'vacio__texto',
        'Cuando una de las páginas cambie, aquí verás cuál fue y qué líneas nuevas aparecieron.'));
      caja.appendChild(vacio);
      listo(caja);
      return;
    }

    var ol = el('ol', 'cambios');
    cambios.forEach(function (c, i) {
      var item = tarjetaCambio(c);
      if (i >= MAX_CAMBIOS) item.hidden = true;
      ol.appendChild(item);
    });
    caja.appendChild(ol);

    if (cambios.length > MAX_CAMBIOS) {
      var boton = el('button', 'boton boton--linea ver-mas',
        'Ver los ' + (cambios.length - MAX_CAMBIOS) + ' anteriores');
      boton.type = 'button';
      boton.addEventListener('click', function () {
        var ocultos = ol.querySelectorAll('li.cambio[hidden]');
        Array.prototype.forEach.call(ocultos, function (li) { li.hidden = false; });
        boton.remove();
      });
      caja.appendChild(boton);
    }
    listo(caja);
  }

  function fallo(idCaja, texto) {
    var caja = $(idCaja);
    vaciar(caja);
    caja.appendChild(el('p', 'aviso aviso--error', texto));
    listo(caja);
  }

  /* ---------- Arranque ---------- */

  var estadoListo = pedir('data/estado.json').then(function (estado) {
    pintarFuentes(estado);
    return estado;
  }).catch(function () {
    fallo('lista-fuentes', 'No pudimos cargar el estado de las páginas. Recarga en un momento; si sigue igual, los avisos siguen llegando por el RSS y por GitHub.');
    $('ultima-revision').textContent = 'no disponible';
    $('n-fuentes').textContent = '–';
    return null;
  });

  estadoListo.then(function (estado) {
    return pedir('data/cambios.json').then(function (datos) {
      pintarCambios(datos, estado);
    }).catch(function () {
      fallo('lista-cambios', 'No pudimos cargar la lista de cambios. Recarga en un momento; si sigue igual, los avisos siguen llegando por el RSS y por GitHub.');
      $('n-cambios').textContent = '–';
    });
  });
})();
