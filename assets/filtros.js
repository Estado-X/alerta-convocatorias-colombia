/* Alerta de convocatorias · Estado X
   Lógica del buscador y los filtros, sin tocar la página (así se puede probar con
   `node --test tests/`). La usa assets/alerta.js en el navegador como window.AlertaFiltros. */
(function (raiz, fabrica) {
  'use strict';
  var modulo = fabrica();
  if (typeof module === 'object' && module.exports) module.exports = modulo;
  else raiz.AlertaFiltros = modulo;
})(this, function () {
  'use strict';

  var MARCAS = /[\u0300-\u036f]/g;

  /* «Becas» y «bécas» se buscan igual: sin tildes y en minúsculas. */
  function normalizar(texto) {
    return String(texto == null ? '' : texto).normalize('NFD').replace(MARCAS, '').toLowerCase();
  }

  /* Palabras de la búsqueda, sin repetir; todas tienen que aparecer. */
  function terminos(consulta) {
    var vistos = [];
    normalizar(consulta).split(/\s+/).forEach(function (t) {
      t = t.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, '');
      if (t && vistos.indexOf(t) === -1) vistos.push(t);
    });
    return vistos;
  }

  function nAgregadas(c) {
    if (typeof c.agregadas_n === 'number') return c.agregadas_n;
    return Array.isArray(c.agregadas) ? c.agregadas.length : 0;
  }

  /* Un cambio en el que solo desaparecieron líneas no trae nada nuevo que leer. */
  function sinNovedades(c) { return nAgregadas(c) === 0; }

  /* Tipos de un cambio: los suyos o, si es anterior a los tipos, los de su página hoy. */
  function tiposDe(c, tiposPorNombre) {
    if (Array.isArray(c.tipos) && c.tipos.length) return c.tipos.slice();
    var deLaFuente = tiposPorNombre && tiposPorNombre[c.nombre];
    return Array.isArray(deLaFuente) ? deLaFuente.slice() : [];
  }

  /* ¿El cambio pasa el filtro de tipo y la búsqueda? Busca en el nombre y en las líneas nuevas. */
  function coincide(c, consulta, tipo, tiposPorNombre) {
    if (tipo && tiposDe(c, tiposPorNombre).indexOf(tipo) === -1) return false;
    var ts = terminos(consulta);
    if (!ts.length) return true;
    var lineas = Array.isArray(c.agregadas) ? c.agregadas : [];
    var todo = normalizar([c.nombre || ''].concat(lineas).join('\n'));
    return ts.every(function (t) { return todo.indexOf(t) !== -1; });
  }

  /* Las líneas nuevas, con las que tienen lo buscado primero. */
  function lineasOrdenadas(c, consulta) {
    var lineas = Array.isArray(c.agregadas) ? c.agregadas.slice() : [];
    var ts = terminos(consulta);
    if (!ts.length) return lineas;
    var con = [], sin = [];
    lineas.forEach(function (l) {
      var n = normalizar(l);
      (ts.some(function (t) { return n.indexOf(t) !== -1; }) ? con : sin).push(l);
    });
    return con.concat(sin);
  }

  /* Parte un texto en tramos para resaltar lo buscado sin usar innerHTML:
     [{texto: 'Beca ', marca: false}, {texto: 'Cauca', marca: true}]. */
  function tramos(texto, consulta) {
    texto = String(texto == null ? '' : texto);
    var ts = terminos(consulta);
    if (!ts.length || !texto) return [{ texto: texto, marca: false }];
    var norm = '', mapa = [];
    for (var i = 0; i < texto.length; i++) {
      var n = normalizar(texto.charAt(i));
      for (var j = 0; j < n.length; j++) { norm += n.charAt(j); mapa.push(i); }
    }
    var marcado = new Array(texto.length);
    ts.forEach(function (t) {
      var desde = 0, pos;
      while ((pos = norm.indexOf(t, desde)) !== -1) {
        for (var k = pos; k < pos + t.length; k++) marcado[mapa[k]] = true;
        desde = pos + t.length;
      }
    });
    var salida = [];
    for (var m = 0; m < texto.length; m++) {
      var marca = !!marcado[m];
      var ultimo = salida[salida.length - 1];
      if (ultimo && ultimo.marca === marca) ultimo.texto += texto.charAt(m);
      else salida.push({ texto: texto.charAt(m), marca: marca });
    }
    return salida;
  }

  /* Páginas distintas con algo nuevo en los últimos 7 días. */
  function resumenSemana(cambios, ahora) {
    var limite = (ahora instanceof Date ? ahora.getTime() : Date.now()) - 7 * 24 * 3600 * 1000;
    var paginas = [];
    (cambios || []).forEach(function (c) {
      var t = Date.parse(c.fecha || '');
      if (isNaN(t) || t < limite || sinNovedades(c)) return;
      var clave = c.slug || c.nombre;
      if (paginas.indexOf(clave) === -1) paginas.push(clave);
    });
    return paginas.length;
  }

  /* Lee fuentes.txt (por si estado.json todavía no trae los tipos): {nombre: [tipos]}. */
  function tiposDesdeFuentes(texto) {
    var salida = {};
    String(texto || '').split(/\r?\n/).forEach(function (linea) {
      linea = linea.trim();
      if (!linea || linea.charAt(0) === '#') return;
      var partes = linea.split('|').map(function (p) { return p.trim(); });
      if (partes.length < 2 || !/^https?:\/\//.test(partes[1])) return;
      var tipos = [];
      partes.slice(2).forEach(function (p) {
        var m = /^tipos?\s*:\s*(.*)$/i.exec(p);
        if (!m) return;
        m[1].split(',').forEach(function (t) {
          t = t.replace(/\s+/g, ' ').trim().slice(0, 40);
          if (t && tipos.indexOf(t) === -1) tipos.push(t);
        });
      });
      salida[partes[0]] = tipos;
    });
    return salida;
  }

  /* Tipos en el orden en que aparecen las páginas. */
  function tiposEnOrden(tiposPorNombre) {
    var vistos = [];
    Object.keys(tiposPorNombre || {}).forEach(function (nombre) {
      tiposPorNombre[nombre].forEach(function (t) { if (vistos.indexOf(t) === -1) vistos.push(t); });
    });
    return vistos;
  }

  return {
    normalizar: normalizar,
    terminos: terminos,
    sinNovedades: sinNovedades,
    tiposDe: tiposDe,
    coincide: coincide,
    lineasOrdenadas: lineasOrdenadas,
    tramos: tramos,
    resumenSemana: resumenSemana,
    tiposDesdeFuentes: tiposDesdeFuentes,
    tiposEnOrden: tiposEnOrden
  };
});
