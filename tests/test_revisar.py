"""Pruebas de scripts/revisar.py, sin red: python -m unittest discover -s tests"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

RAIZ = Path(__file__).resolve().parent.parent


def cargar(lista: str | None = None, raiz: Path | None = None):
    """Carga revisar.py como módulo nuevo (con o sin --lista) sin tocar sys.argv de verdad."""
    argumentos = [str(RAIZ / "scripts" / "revisar.py")] + (["--lista", lista] if lista else [])
    ruta = (raiz or RAIZ) / "scripts" / "revisar.py"
    spec = importlib.util.spec_from_file_location(f"revisar_{lista or 'principal'}_{id(argumentos)}", ruta)
    modulo = importlib.util.module_from_spec(spec)
    with mock.patch.object(sys, "argv", argumentos):
        spec.loader.exec_module(modulo)
    return modulo


class Respuesta(io.BytesIO):
    def __init__(self, cuerpo: bytes, charset: str = "utf-8"):
        super().__init__(cuerpo)
        self.headers = mock.Mock(get_content_charset=lambda: charset)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


R = cargar()
AHORA = datetime(2027, 1, 22, 15, 0, tzinfo=timezone.utc)


class Fuentes(unittest.TestCase):
    def test_formato_viejo_sigue_sirviendo(self):
        self.assertEqual(R.partes_opcionales([]), ([], []))
        self.assertEqual(R.partes_opcionales(["Beca, Convocatoria"]), (["beca", "convocatoria"], []))

    def test_tipo_en_cualquier_columna(self):
        self.assertEqual(R.partes_opcionales(["tipo: Becas"]), ([], ["Becas"]))
        self.assertEqual(R.partes_opcionales(["doctor, beca", "tipo: En Colombia, En el exterior"]),
                         (["doctor", "beca"], ["En Colombia", "En el exterior"]))
        self.assertEqual(R.partes_opcionales(["Tipos:  Paz ,  Paz, Cooperación "]), ([], ["Paz", "Cooperación"]))

    def test_tipo_vacio_no_rompe(self):
        self.assertEqual(R.partes_opcionales(["tipo:"]), ([], []))
        self.assertEqual(R.partes_opcionales([""]), ([], []))

    def test_las_listas_del_repo_tienen_tipo(self):
        for lista in (None, "doctorado"):
            modulo = cargar(lista)
            fuentes = modulo.leer_fuentes()
            self.assertTrue(fuentes, lista)
            for f in fuentes:
                self.assertTrue(f["tipos"], f"{f['nombre']} no tiene tipo")
                self.assertTrue(f["url"].startswith("https://"))
        principal = cargar().leer_fuentes()
        self.assertEqual(len(principal), 9)
        self.assertEqual(cargar().tipos_en_orden(principal),
                         ["Ciencia y tecnología", "Cooperación", "Becas", "Emprendimiento", "Paz"])
        doctorado = cargar("doctorado").leer_fuentes()
        self.assertEqual(len(doctorado), 8)
        self.assertEqual(doctorado[0]["palabras"], ["doctor", "beca", "formacion", "maestria"])

    def test_los_slugs_no_cambian_con_el_tipo(self):
        # Los textos guardados en data/textos/ se buscan por slug: agregar tipos no puede moverlos.
        for f in cargar().leer_fuentes():
            self.assertTrue((RAIZ / "data" / "textos" / f"{f['slug']}.txt").exists(), f["slug"])
        for f in cargar("doctorado").leer_fuentes():
            self.assertTrue((RAIZ / "data" / "doctorado" / "textos" / f"{f['slug']}.txt").exists(), f["slug"])


class Ruido(unittest.TestCase):
    def test_fecha_de_actualizacion_es_ruido(self):
        pagina = "<p>Fecha de actualización: 18 de noviembre de 2026</p><p>Doctorados en China (2027)</p>"
        self.assertEqual(R.lineas_visibles(pagina, []), ["Doctorados en China (2027)"])

    def test_comparar(self):
        agregadas, quitadas = R.comparar(["a uno", "b dos"], ["b dos", "c tres"])
        self.assertEqual(agregadas, ["c tres"])
        self.assertEqual(quitadas, ["a uno"])

    def test_sin_novedades(self):
        self.assertTrue(R.sin_novedades({"agregadas": [], "agregadas_n": 0, "quitadas_n": 1}))
        self.assertFalse(R.sin_novedades({"agregadas": ["x"], "agregadas_n": 1, "quitadas_n": 0}))
        self.assertTrue(R.sin_novedades({"agregadas": []}))  # datos viejos sin agregadas_n
        self.assertFalse(R.sin_novedades({"agregadas": ["x"]}))


def cambio(agregadas=("Convocatoria nueva 2027",), quitadas=0, **extra):
    c = {
        "slug": "icetex-becas-vigentes-en-el-exterior", "nombre": "ICETEX · becas", "url": "https://web.icetex.gov.co/",
        "fecha": "2027-01-22T15:00:00+00:00", "fecha_texto": "22/01/2027 10:00 a. m.",
        "agregadas": list(agregadas), "agregadas_n": len(agregadas), "quitadas_n": quitadas,
    }
    c.update(extra)
    return c


ENTORNO = {"GITHUB_TOKEN": "x", "GITHUB_REPOSITORY": "Estado-X/alerta-convocatorias-colombia", "AVISOS_GITHUB": "si"}


class Avisos(unittest.TestCase):
    def test_solo_quitadas_no_abre_aviso(self):
        with mock.patch.dict(os.environ, ENTORNO), mock.patch("urllib.request.urlopen") as red:
            self.assertIsNone(R.abrir_aviso(cambio(agregadas=(), quitadas=3)))
            red.assert_not_called()

    def test_cambio_real_abre_aviso_con_etiqueta(self):
        pedidos = []

        def responder(pedido, timeout=None):
            pedidos.append(pedido)
            return Respuesta(json.dumps({"html_url": "https://github.com/x/y/issues/20"}).encode())

        with mock.patch.dict(os.environ, ENTORNO), mock.patch("urllib.request.urlopen", responder):
            self.assertEqual(R.abrir_aviso(cambio()), "https://github.com/x/y/issues/20")
        datos = json.loads(pedidos[0].data)
        self.assertEqual(datos["labels"], ["aviso"])
        self.assertTrue(datos["title"].startswith("Cambió: ICETEX"))
        self.assertIn("```text\nConvocatoria nueva 2027\n```", datos["body"])

    def test_lista_aparte_lleva_su_etiqueta(self):
        self.assertEqual(cargar("doctorado").etiquetas_aviso(), ["aviso", "doctorado"])

    def test_menciones_quedan_en_bloque_de_codigo(self):
        cuerpo = R.cuerpo_aviso(cambio(agregadas=("Escribe a @SENA ```fin",)))
        self.assertIn("```text\nEscribe a @SENA '''fin\n```", cuerpo)

    def test_si_la_etiqueta_falla_sale_sin_etiqueta(self):
        pedidos = []

        def responder(pedido, timeout=None):
            pedidos.append(json.loads(pedido.data))
            if "labels" in pedidos[-1]:
                raise urllib.error.HTTPError(pedido.full_url, 422, "Unprocessable", {}, None)
            return Respuesta(b'{"html_url": "https://github.com/x/y/issues/21"}')

        with mock.patch.dict(os.environ, ENTORNO), mock.patch("urllib.request.urlopen", responder):
            self.assertEqual(R.abrir_aviso(cambio()), "https://github.com/x/y/issues/21")
        self.assertEqual(len(pedidos), 2)
        self.assertNotIn("labels", pedidos[1])

    def test_sin_token_no_hay_aviso(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""}):
            self.assertIsNone(R.abrir_aviso(cambio()))
        with mock.patch.dict(os.environ, dict(ENTORNO, AVISOS_GITHUB="no")):
            self.assertIsNone(R.abrir_aviso(cambio()))


def issue(numero, titulo, dias, login="github-actions[bot]", **extra):
    creado = (AHORA - timedelta(days=dias)).isoformat().replace("+00:00", "Z")
    d = {"number": numero, "title": titulo, "created_at": creado, "state": "open", "user": {"login": login}}
    d.update(extra)
    return d


class CerrarAvisos(unittest.TestCase):
    ISSUES = [
        issue(1, "Cambió: APC-Colombia · 14/11/2026", 69),
        issue(6, "Doctorado · Cambió: ICETEX · 18/11/2026", 65),
        issue(12, "Cambió: ICETEX · 25/11/2026", 29),                       # todavía no cumple 30 días
        issue(30, "Cambió: algo que escribió una persona", 90, login="alguien"),
        issue(31, "Propón una página: Gobernación del Cauca", 90, login="github-actions[bot]"),
        issue(32, "Cambió: un PR", 90, pull_request={"url": "x"}),
    ]

    def test_principal_cierra_solo_los_suyos(self):
        self.assertEqual([i["number"] for i in R.avisos_por_cerrar(self.ISSUES, AHORA)], [1])

    def test_doctorado_cierra_solo_los_suyos(self):
        self.assertEqual([i["number"] for i in cargar("doctorado").avisos_por_cerrar(self.ISSUES, AHORA)], [6])

    def test_cero_dias_no_cierra(self):
        self.assertEqual(R.avisos_por_cerrar(self.ISSUES, AHORA, 0), [])

    def test_los_14_avisos_viejos_se_cierran_el_22_de_enero(self):
        # Los avisos #1 a #14 se abrieron entre el 14 y el 25 nov 2026: el 22 ene 2027 ya tienen más de 30 días.
        viejos = [issue(n, f"Cambió: X · {n}", (AHORA - datetime(2026, 11, 25, 20, 24, tzinfo=timezone.utc)).days)
                  for n in range(1, 15)]
        self.assertEqual(len(R.avisos_por_cerrar(viejos, AHORA)), 14)

    def test_cierra_comentando_y_sin_tumbar_la_corrida(self):
        llamadas = []

        def responder(pedido, timeout=None):
            llamadas.append((pedido.get_method(), pedido.full_url))
            if pedido.get_method() == "GET":
                return Respuesta(json.dumps(self.ISSUES).encode())
            return Respuesta(b"{}")

        with mock.patch.dict(os.environ, ENTORNO), mock.patch("urllib.request.urlopen", responder), \
                mock.patch.object(R, "datetime", wraps=datetime):
            self.assertEqual(R.cerrar_avisos_viejos(AHORA), 1)
        metodos = [m for m, _ in llamadas]
        self.assertEqual(metodos, ["GET", "POST", "PATCH"])
        self.assertTrue(llamadas[1][1].endswith("/issues/1/comments"))
        self.assertTrue(llamadas[2][1].endswith("/issues/1"))

    def test_si_la_red_falla_no_cierra_nada(self):
        def fallar(pedido, timeout=None):
            raise OSError("sin red")

        with mock.patch.dict(os.environ, ENTORNO), mock.patch("urllib.request.urlopen", fallar):
            self.assertEqual(R.cerrar_avisos_viejos(AHORA), 0)


class Rss(unittest.TestCase):
    def test_sin_novedades_no_entra_al_rss(self):
        xml = R.feed([cambio(), cambio(agregadas=(), quitadas=2, slug="otra")], AHORA)
        self.assertEqual(xml.count("<entry>"), 1)

    def test_rss_por_tipo(self):
        self.assertEqual(R.ruta_feed_tipo("Ciencia y tecnología"), "data/feed-ciencia-y-tecnologia.xml")
        self.assertEqual(cargar("doctorado").ruta_feed_tipo("En el exterior"), "data/doctorado/feed-en-el-exterior.xml")
        xml = R.feed([cambio()], AHORA, "Becas")
        self.assertIn("<title>Alerta de convocatorias · Becas · Estado X</title>", xml)
        self.assertIn("data/feed-becas.xml", xml)

    def test_tipos_de_cambios_viejos_salen_de_su_fuente(self):
        tipos = {"icetex-becas-vigentes-en-el-exterior": ["Becas"]}
        self.assertEqual(R.tipos_de(cambio(), tipos), ["Becas"])
        self.assertEqual(R.tipos_de(cambio(tipos=["Paz"]), tipos), ["Paz"])
        self.assertEqual(R.tipos_de(cambio(slug="otra"), tipos), [])


class CorridaCompleta(unittest.TestCase):
    """Corre main() en una copia del repo, con páginas falsas, para ver los archivos que escribe."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        for nombre in ("scripts", "data"):
            shutil.copytree(RAIZ / nombre, self.dir / nombre)
        for nombre in ("fuentes.txt", "fuentes-doctorado.txt"):
            shutil.copy(RAIZ / nombre, self.dir / nombre)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def correr(self, lista=None, paginas=None):
        modulo = cargar(lista, self.dir)
        textos = modulo.TEXTOS

        def descargar(url):
            if paginas and url in paginas:
                return paginas[url]
            # Por defecto, la misma página que quedó guardada: sin cambios.
            slug = next(f["slug"] for f in modulo.leer_fuentes() if f["url"] == url)
            lineas = (textos / f"{slug}.txt").read_text(encoding="utf-8").splitlines()
            return "".join(f"<p>{l}</p>" for l in lineas)

        with mock.patch.object(modulo, "descargar", descargar), \
                mock.patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_REPOSITORY": ""}), \
                mock.patch("sys.stdout", io.StringIO()):
            modulo.main()
        return modulo

    def test_dos_corridas_sin_cambios_falsos(self):
        antes = json.loads((self.dir / "data" / "cambios.json").read_text(encoding="utf-8"))["cambios"]
        self.correr()   # la primera puede anotar «sin novedades» por la regla nueva de ruido
        self.correr()
        despues = json.loads((self.dir / "data" / "cambios.json").read_text(encoding="utf-8"))["cambios"]
        nuevos = despues[: len(despues) - len(antes)]
        self.assertTrue(all(c["sin_novedades"] for c in nuevos), nuevos)
        corrida = json.loads((self.dir / "data" / "corrida.json").read_text(encoding="utf-8"))
        self.assertEqual(corrida["cambios"], 0)
        self.assertEqual(corrida["con_novedades"], 0)

    def test_escribe_tipos_y_rss_por_tipo(self):
        self.correr()
        estado = json.loads((self.dir / "data" / "estado.json").read_text(encoding="utf-8"))
        self.assertEqual([t["nombre"] for t in estado["tipos"]],
                         ["Ciencia y tecnología", "Cooperación", "Becas", "Emprendimiento", "Paz"])
        for t in estado["tipos"]:
            self.assertTrue((self.dir / t["feed"]).exists(), t["feed"])
        becas = (self.dir / "data" / "feed-becas.xml").read_text(encoding="utf-8")
        self.assertIn("ICETEX", becas)
        self.assertNotIn("MinCiencias", becas)
        self.correr("doctorado")
        estado = json.loads((self.dir / "data" / "doctorado" / "estado.json").read_text(encoding="utf-8"))
        self.assertEqual([t["nombre"] for t in estado["tipos"]], ["En Colombia", "En el exterior"])

    def test_cambio_real_y_solo_quitadas(self):
        modulo = cargar(None, self.dir)
        fuentes = {f["slug"]: f for f in modulo.leer_fuentes()}
        fondo = fuentes["fondo-emprender-sena"]
        mintic = fuentes["mintic-convocatorias"]
        texto_fondo = (modulo.TEXTOS / "fondo-emprender-sena.txt").read_text(encoding="utf-8").splitlines()
        texto_mintic = (modulo.TEXTOS / "mintic-convocatorias.txt").read_text(encoding="utf-8").splitlines()
        paginas = {
            fondo["url"]: "".join(f"<p>{l}</p>" for l in texto_fondo) + "<p>Convocatoria nacional 2027 para el Cauca</p>",
            mintic["url"]: "".join(f"<p>{l}</p>" for l in texto_mintic[:-1]),
        }
        previo = json.loads((self.dir / "data" / "estado.json").read_text(encoding="utf-8"))
        mintic_previo = next(f for f in previo["fuentes"] if f["slug"] == "mintic-convocatorias")
        self.correr(paginas=paginas)
        cambios = json.loads((self.dir / "data" / "cambios.json").read_text(encoding="utf-8"))["cambios"]
        por_slug = {}
        for c in cambios:  # el más nuevo de cada página
            por_slug.setdefault(c["slug"], c)
        self.assertFalse(por_slug["fondo-emprender-sena"]["sin_novedades"])
        self.assertEqual(por_slug["fondo-emprender-sena"]["tipos"], ["Emprendimiento"])
        self.assertTrue(por_slug["mintic-convocatorias"]["sin_novedades"])
        estado = json.loads((self.dir / "data" / "estado.json").read_text(encoding="utf-8"))
        mintic_estado = next(f for f in estado["fuentes"] if f["slug"] == "mintic-convocatorias")
        # «sin novedades» no mueve el último cambio de la página
        self.assertEqual(mintic_estado["ultimo_cambio"], mintic_previo.get("ultimo_cambio"))
        feed = (self.dir / "data" / "feed-emprendimiento.xml").read_text(encoding="utf-8")
        self.assertIn("Convocatoria nacional 2027 para el Cauca", feed)
        principal = (self.dir / "data" / "feed.xml").read_text(encoding="utf-8")
        self.assertEqual(principal.count("<entry>"), sum(1 for c in cambios[:50] if c.get("agregadas_n")))


class TiposSinRepetir(unittest.TestCase):
    def test_mismo_tipo_escrito_distinto(self):
        self.assertEqual(R.partes_opcionales(["tipo: Becas, becas, BÉCAS"]), ([], ["Becas"]))
        fuentes = [{"tipos": ["Becas"]}, {"tipos": ["becas", "Paz"]}, {"tipos": ["PAZ"]}]
        self.assertEqual(R.tipos_en_orden(fuentes), ["Becas", "Paz"])

    def test_rss_por_tipo_compara_sin_mayusculas(self):
        self.assertTrue(R.es_del_tipo(cambio(tipos=["becas"]), "Becas", {}))
        self.assertFalse(R.es_del_tipo(cambio(tipos=["Paz"]), "Becas", {}))


class CierreConTope(unittest.TestCase):
    def test_los_mas_viejos_primero_y_maximo_diez(self):
        issues = [issue(n, f"Cambió: X · {n}", 31 + (n % 7)) for n in range(1, 16)]
        cerrar = R.avisos_por_cerrar(issues, AHORA)
        fechas = [i["created_at"] for i in cerrar]
        self.assertEqual(fechas, sorted(fechas))
        llamadas = []

        def responder(pedido, timeout=None):
            llamadas.append((pedido.get_method(), pedido.full_url))
            if pedido.get_method() == "GET":
                return Respuesta(json.dumps(issues).encode())
            return Respuesta(b"{}")

        with mock.patch.dict(os.environ, ENTORNO), mock.patch("urllib.request.urlopen", responder):
            self.assertEqual(R.cerrar_avisos_viejos(AHORA), R.CIERRES_POR_CORRIDA)
        self.assertIn("sort=created&direction=asc", llamadas[0][1])
        cerrados = [u for m, u in llamadas if m == "PATCH"]
        self.assertEqual(len(cerrados), 10)
        primero = cerrar[0]["number"]
        self.assertTrue(cerrados[0].endswith(f"/issues/{primero}"))


class Caidas(unittest.TestCase):
    def test_parece_caida(self):
        base = [f"línea {i}" for i in range(20)]
        self.assertTrue(R.parece_caida(base, base[:7]))       # bajó 65 %
        self.assertFalse(R.parece_caida(base, base[:9]))      # bajó 55 %
        self.assertFalse(R.parece_caida(base[:9], []))        # base chica: no aplica


class CorridaConCaidasYVistas(CorridaCompleta):
    # Usa la copia del repo de CorridaCompleta, sin repetir sus pruebas.
    test_dos_corridas_sin_cambios_falsos = None
    test_escribe_tipos_y_rss_por_tipo = None
    test_cambio_real_y_solo_quitadas = None

    def leer(self, *ruta):
        return json.loads((self.dir.joinpath("data", *ruta)).read_text(encoding="utf-8"))

    def test_pagina_de_error_con_200_no_pisa_la_base(self):
        modulo = cargar(None, self.dir)
        fondo = next(f for f in modulo.leer_fuentes() if f["slug"] == "fondo-emprender-sena")
        base = (modulo.TEXTOS / "fondo-emprender-sena.txt").read_text(encoding="utf-8")
        error = {fondo["url"]: "<p>Sitio en mantenimiento</p><p>Vuelva más tarde, por favor</p>"}
        antes = len(self.leer("cambios.json")["cambios"])

        for vez in (1, 2):
            self.correr(paginas=error)
            self.assertEqual((modulo.TEXTOS / "fondo-emprender-sena.txt").read_text(encoding="utf-8"), base)
            ficha = next(f for f in self.leer("estado.json")["fuentes"] if f["slug"] == "fondo-emprender-sena")
            self.assertEqual(ficha["estado"], "error")
            self.assertEqual(ficha["caidas_seguidas"], vez)
            self.assertIn("mantenimiento", ficha["error"])
            nuevos = self.leer("cambios.json")["cambios"][: len(self.leer("cambios.json")["cambios"]) - antes]
            self.assertFalse([c for c in nuevos if c["slug"] == "fondo-emprender-sena"])

        # Si vuelve la página de siempre, no hay aviso falso y el contador vuelve a cero.
        self.correr()
        ficha = next(f for f in self.leer("estado.json")["fuentes"] if f["slug"] == "fondo-emprender-sena")
        self.assertEqual(ficha["estado"], "ok")
        self.assertEqual(ficha["caidas_seguidas"], 0)
        nuevos = self.leer("cambios.json")["cambios"][: len(self.leer("cambios.json")["cambios"]) - antes]
        self.assertFalse([c for c in nuevos if c["slug"] == "fondo-emprender-sena"])

    def test_tres_caidas_seguidas_se_aceptan(self):
        modulo = cargar(None, self.dir)
        fondo = next(f for f in modulo.leer_fuentes() if f["slug"] == "fondo-emprender-sena")
        corta = {fondo["url"]: "<p>Sitio en mantenimiento</p><p>Vuelva más tarde, por favor</p>"}
        for _ in range(3):
            self.correr(paginas=corta)
        self.assertEqual((modulo.TEXTOS / "fondo-emprender-sena.txt").read_text(encoding="utf-8").splitlines(),
                         ["Sitio en mantenimiento", "Vuelva más tarde, por favor"])
        ultimo = next(c for c in self.leer("cambios.json")["cambios"] if c["slug"] == "fondo-emprender-sena")
        self.assertEqual(ultimo["agregadas_n"], 2)

    def test_linea_que_vuelve_antes_de_30_dias_no_es_nueva(self):
        modulo = cargar(None, self.dir)
        fondo = next(f for f in modulo.leer_fuentes() if f["slug"] == "fondo-emprender-sena")
        lineas = (modulo.TEXTOS / "fondo-emprender-sena.txt").read_text(encoding="utf-8").splitlines()
        sin_ultima = {fondo["url"]: "".join(f"<p>{l}</p>" for l in lineas[:-1])}
        self.correr()                      # guarda las líneas vistas
        previos = len([c for c in self.leer("cambios.json")["cambios"] if c["slug"] == "fondo-emprender-sena"])
        self.correr(paginas=sin_ultima)    # desaparece una línea: «sin novedades»
        self.correr()                      # vuelve: no es nueva
        cambios = [c for c in self.leer("cambios.json")["cambios"] if c["slug"] == "fondo-emprender-sena"]
        # Solo queda la revisión en que desapareció; cuando volvió no se anotó nada ni hubo aviso.
        self.assertEqual(len(cambios) - previos, 1)
        self.assertTrue(cambios[0]["sin_novedades"])

    def test_linea_vista_hace_mas_de_30_dias_si_es_nueva(self):
        modulo = cargar(None, self.dir)
        fondo = next(f for f in modulo.leer_fuentes() if f["slug"] == "fondo-emprender-sena")
        lineas = (modulo.TEXTOS / "fondo-emprender-sena.txt").read_text(encoding="utf-8").splitlines()
        vieja = "Convocatoria nacional 2027 para el Cauca"
        (modulo.TEXTOS / "fondo-emprender-sena.vistas.json").write_text(
            json.dumps({modulo.clave_linea(vieja): "2000-01-01"}), encoding="utf-8")
        self.correr(paginas={fondo["url"]: "".join(f"<p>{l}</p>" for l in lineas + [vieja])})
        ultimo = next(c for c in self.leer("cambios.json")["cambios"] if c["slug"] == "fondo-emprender-sena")
        self.assertEqual(ultimo["agregadas"], [vieja])
        vistas = json.loads((modulo.TEXTOS / "fondo-emprender-sena.vistas.json").read_text(encoding="utf-8"))
        self.assertNotIn("2000-01-01", vistas.values())  # las viejas se limpian


if __name__ == "__main__":
    unittest.main()
