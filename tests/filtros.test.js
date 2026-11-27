// Pruebas del buscador y los filtros de la página: node --test tests/
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const F = require('../assets/filtros.js');

const icetex = {
  slug: 'icetex', nombre: 'ICETEX · becas vigentes en el exterior', fecha: '2027-01-20T20:00:00+00:00',
  agregadas: ['Fecha de cierre: 3 de marzo', 'Beca para mujeres líderes del Cauca'], agregadas_n: 2, quitadas_n: 1
};
const paz = {
  slug: 'onu', nombre: 'Fondo Multidonante de la ONU para la Paz', fecha: '2027-01-19T20:00:00+00:00',
  agregadas: ['Convocatoria para organizaciones de víctimas'], agregadas_n: 1, quitadas_n: 0, tipos: ['Paz', 'Cooperación']
};
const vacio = {
  slug: 'minciencias', nombre: 'MinCiencias · convocatorias', fecha: '2027-01-21T20:00:00+00:00',
  agregadas: [], agregadas_n: 0, quitadas_n: 1
};
const tipos = { 'ICETEX · becas vigentes en el exterior': ['Becas'], 'MinCiencias · convocatorias': ['Ciencia y tecnología'] };

test('normaliza sin tildes ni mayúsculas', () => {
  assert.equal(F.normalizar('Bécas CÁUCA ñ'), 'becas cauca n');
  assert.deepEqual(F.terminos('  beca, beca Cauca. '), ['beca', 'cauca']);
  assert.deepEqual(F.terminos(''), []);
});

test('sin novedades: solo desaparecieron líneas', () => {
  assert.equal(F.sinNovedades(vacio), true);
  assert.equal(F.sinNovedades(icetex), false);
  assert.equal(F.sinNovedades({ agregadas: [] }), true);
});

test('los tipos salen del cambio o de su página', () => {
  assert.deepEqual(F.tiposDe(paz, tipos), ['Paz', 'Cooperación']);
  assert.deepEqual(F.tiposDe(icetex, tipos), ['Becas']);
  assert.deepEqual(F.tiposDe({ nombre: 'otra' }, tipos), []);
});

test('busca en las líneas nuevas y en el nombre, sin tildes, con todas las palabras', () => {
  assert.equal(F.coincide(icetex, 'beca', '', tipos), true);
  assert.equal(F.coincide(icetex, 'cáuca mujeres', '', tipos), true);
  assert.equal(F.coincide(icetex, 'cauca paz', '', tipos), false);
  assert.equal(F.coincide(paz, 'victimas', '', tipos), true);
  assert.equal(F.coincide(paz, 'ONU', '', tipos), true);
  assert.equal(F.coincide(paz, '', '', tipos), true);
});

test('filtra por tipo y combina con la búsqueda', () => {
  assert.equal(F.coincide(icetex, '', 'Becas', tipos), true);
  assert.equal(F.coincide(icetex, '', 'Paz', tipos), false);
  assert.equal(F.coincide(paz, 'organizaciones', 'Cooperación', tipos), true);
  assert.equal(F.coincide(paz, 'beca', 'Cooperación', tipos), false);
});

test('las líneas con lo buscado van primero', () => {
  assert.deepEqual(F.lineasOrdenadas(icetex, 'cauca'), ['Beca para mujeres líderes del Cauca', 'Fecha de cierre: 3 de marzo']);
  assert.deepEqual(F.lineasOrdenadas(icetex, ''), icetex.agregadas);
});

test('resalta lo buscado respetando tildes del original', () => {
  assert.deepEqual(F.tramos('Víctimas del conflicto', 'victimas'), [
    { texto: 'Víctimas', marca: true }, { texto: ' del conflicto', marca: false }
  ]);
  assert.deepEqual(F.tramos('Beca en Cauca', 'beca cauca'), [
    { texto: 'Beca', marca: true }, { texto: ' en ', marca: false }, { texto: 'Cauca', marca: true }
  ]);
  assert.deepEqual(F.tramos('Sin nada', ''), [{ texto: 'Sin nada', marca: false }]);
  assert.equal(F.tramos('Año 2027', 'ano').map((t) => t.texto).join(''), 'Año 2027');
  assert.deepEqual(F.tramos('Año', 'ano'), [{ texto: 'Año', marca: true }]);
});

test('resumen de la semana: páginas distintas con algo nuevo', () => {
  const ahora = new Date('2027-01-22T15:00:00Z');
  const viejo = Object.assign({}, paz, { fecha: '2027-01-01T00:00:00Z' });
  assert.equal(F.resumenSemana([vacio, icetex, icetex, paz, viejo], ahora), 2);
  assert.equal(F.resumenSemana([], ahora), 0);
});

test('lee los tipos de fuentes.txt, también con el formato viejo', () => {
  const texto = [
    '# comentario | https://no.cuenta',
    'MinCiencias · convocatorias | https://minciencias.gov.co/x | tipo: Ciencia y tecnología',
    'Colfuturo | https://www.colfuturo.org/x | doctor, beca | tipo: En el exterior, En el exterior',
    'Vieja | https://ejemplo.org',
    'rota sin dirección'
  ].join('\r\n');
  const leidos = F.tiposDesdeFuentes(texto);
  assert.deepEqual(leidos, {
    'MinCiencias · convocatorias': ['Ciencia y tecnología'],
    Colfuturo: ['En el exterior'],
    Vieja: []
  });
  assert.deepEqual(F.tiposEnOrden(leidos), ['Ciencia y tecnología', 'En el exterior']);
});
