#!/usr/bin/env python3
"""Alerta de convocatorias · revisa las páginas de fuentes.txt y avisa cuando cambian.

Solo usa la biblioteca estándar de Python. Para cada página:
  1. la descarga (forzando IPv4, porque GitHub Actions no alcanza algunos
     servidores que publican IPv6),
  2. saca el texto visible, línea por línea,
  3. lo compara con el texto de la revisión anterior (data/textos/),
  4. si aparecieron o desaparecieron líneas, registra el cambio.

Escribe data/estado.json, data/cambios.json, data/corrida.json y data/feed.xml.
Si corre en GitHub Actions con permiso para abrir avisos (issues), abre uno
por cada página que cambió; quien siga el repo lo recibe por correo.

Hecho por Estado X (2026), licencia MIT. Inspirado en changedetection.io,
de dgtlmoon (licencia Apache-2.0); no reutiliza su código.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FUENTES = RAIZ / "fuentes.txt"
DATOS = RAIZ / "data"
TEXTOS = DATOS / "textos"

BOGOTA = timezone(timedelta(hours=-5), "Bogotá")
AGENTE = "Mozilla/5.0 (compatible; AlertaConvocatorias/1.0; +https://github.com/Estado-X/alerta-convocatorias-colombia)"
TIEMPO_ESPERA = 40      # segundos por cada lectura de la red
TIEMPO_TOTAL = 90       # segundos como máximo para descargar una página completa
TAMANO_MAXIMO = 8_000_000
MAX_CAMBIOS = 300
MAX_LINEAS_AVISO = 20
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sitio() -> str:
    """Dirección de la página: SITIO_URL si existe; si no, la de GitHub Pages del repo."""
    if os.environ.get("SITIO_URL"):
        return os.environ["SITIO_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY", "Estado-X/alerta-convocatorias-colombia")
    dueno, _, nombre = repo.partition("/")
    return f"https://{dueno.lower()}.github.io/{nombre}/"


SITIO = _sitio()

# Líneas que cambian solas y no dicen nada (contadores, horas de la página, etc.).
RUIDO = [
    re.compile(p, re.I)
    for p in (
        r"^hace \d+ (segundos?|minutos?|horas?|d[ií]as?)$",
        r"^\d+ (visitas|vistas|views)$",
        r"^(visitas|vistas|views)\s*:?\s*\d+",
        r"(última|ultima) actualizaci[oó]n",
        r"^(cargando|loading)\b",
        r"^copyright\b|^©",
    )
]


# --- red ---------------------------------------------------------------------

_getaddrinfo = socket.getaddrinfo


def _solo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
    return _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = _solo_ipv4


def descargar(url: str) -> str:
    pedido = urllib.request.Request(url, headers={"User-Agent": AGENTE, "Accept-Language": "es-CO,es;q=0.9"})
    limite = time.monotonic() + TIEMPO_TOTAL
    partes: list[bytes] = []
    leidos = 0
    with urllib.request.urlopen(pedido, timeout=TIEMPO_ESPERA) as resp:
        while leidos < TAMANO_MAXIMO:
            if time.monotonic() > limite:
                raise TimeoutError(f"la página tardó más de {TIEMPO_TOTAL} s en llegar")
            trozo = resp.read(65_536)
            if not trozo:
                break
            partes.append(trozo)
            leidos += len(trozo)
        juego = resp.headers.get_content_charset()
    crudo = b"".join(partes)
    if not juego:  # sin charset en el encabezado: se busca en <meta>
        meta = re.search(rb"<meta[^>]+charset=[\"']?([A-Za-z0-9_\-]+)", crudo[:8192], re.I)
        juego = meta.group(1).decode("ascii") if meta else "utf-8"
    try:
        return crudo.decode(juego, errors="replace")
    except LookupError:
        return crudo.decode("utf-8", errors="replace")


# --- texto visible ------------------------------------------------------------

BLOQUES = {
    "p", "div", "li", "ul", "ol", "tr", "td", "th", "table", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "br", "dt", "dd", "blockquote", "main", "aside",
}
SALTAR = {"script", "style", "noscript", "svg", "head", "template", "iframe", "nav", "header", "footer", "form", "select", "button"}


class Texto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.saltando = 0
        self.partes: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SALTAR:
            self.saltando += 1
        elif tag in BLOQUES:
            self.partes.append("\n")

    def handle_endtag(self, tag):
        if tag in SALTAR and self.saltando:
            self.saltando -= 1
        elif tag in BLOQUES:
            self.partes.append("\n")

    def handle_data(self, data):
        if not self.saltando:
            self.partes.append(data)


def lineas_visibles(pagina: str, palabras: list[str]) -> list[str]:
    lector = Texto()
    try:
        lector.feed(pagina)
        lector.close()
    except Exception:  # HTML muy roto: se sigue con lo que alcanzó a leer
        pass
    vistas: set[str] = set()
    salida: list[str] = []
    for linea in "".join(lector.partes).split("\n"):
        linea = re.sub(r"\s+", " ", CONTROL.sub(" ", html.unescape(linea))).strip()
        if len(linea) < 4 or any(r.search(linea) for r in RUIDO):
            continue
        if palabras and not any(p in sin_tildes(linea) for p in palabras):
            continue
        if linea not in vistas:
            vistas.add(linea)
            salida.append(linea)
    return salida


# --- utilidades ---------------------------------------------------------------

def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def slug(nombre: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", sin_tildes(nombre)).strip("-")
    return s[:60] or hashlib.sha1(nombre.encode()).hexdigest()[:10]


def fecha_texto(momento: datetime) -> str:
    local = momento.astimezone(BOGOTA)
    hora = local.hour % 12 or 12
    sufijo = "a. m." if local.hour < 12 else "p. m."
    return f"{local:%d/%m/%Y} {hora}:{local:%M} {sufijo}"


def leer_json(ruta: Path, por_defecto):
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return por_defecto


def escribir(ruta: Path, contenido: str) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    temporal.write_text(contenido, encoding="utf-8")
    temporal.replace(ruta)


def leer_fuentes() -> list[dict]:
    fuentes = []
    usados: set[str] = set()
    for numero, linea in enumerate(FUENTES.read_text(encoding="utf-8").splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) < 2 or not partes[1].startswith(("http://", "https://")):
            print(f"fuentes.txt, línea {numero}: no la entiendo, la salto ({linea[:60]})", file=sys.stderr)
            continue
        palabras = [sin_tildes(p.strip()) for p in partes[2].split(",") if p.strip()] if len(partes) > 2 else []
        base = slug(partes[0])
        nombre_archivo, n = base, 2
        while nombre_archivo in usados:  # dos nombres parecidos no se pisan el archivo
            nombre_archivo = f"{base}-{n}"
            n += 1
        if nombre_archivo != base:
            print(f"fuentes.txt, línea {numero}: el nombre se parece a otro; lo guardo como {nombre_archivo}", file=sys.stderr)
        usados.add(nombre_archivo)
        fuentes.append({"nombre": partes[0], "url": partes[1], "palabras": palabras, "slug": nombre_archivo})
    return fuentes


# --- avisos en GitHub ---------------------------------------------------------

def abrir_aviso(cambio: dict) -> str | None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo or os.environ.get("AVISOS_GITHUB", "si").lower() in ("no", "0", "false"):
        return None
    # El texto viene de páginas de terceros: va dentro de un bloque de código para que
    # GitHub no lo convierta en menciones (@cuenta), referencias (repo#1) ni enlaces.
    seguras = [l.replace("`", "'") for l in cambio["agregadas"][:MAX_LINEAS_AVISO]]
    lineas = "```text\n" + ("\n".join(seguras) or "(solo desaparecieron líneas)") + "\n```"
    mas = cambio["agregadas_n"] - min(cambio["agregadas_n"], MAX_LINEAS_AVISO)
    cuerpo = (
        f"**{cambio['nombre']}** cambió (revisión del {cambio['fecha_texto']}).\n\n"
        f"Página: {cambio['url']}\n\n"
        f"Líneas nuevas ({cambio['agregadas_n']}):\n\n{lineas}\n"
        + (f"\n…y {mas} más.\n" if mas > 0 else "")
        + f"\nLíneas que ya no están: {cambio['quitadas_n']}.\n\n"
        f"Abre la página oficial para leer la convocatoria completa: este aviso solo dice que algo cambió.\n\n"
        f"Todas las páginas: {SITIO}"
    )
    datos = json.dumps({"title": f"Cambió: {cambio['nombre']} · {cambio['fecha_texto'][:10]}", "body": cuerpo}).encode()
    pedido = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=datos,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": AGENTE,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(pedido, timeout=TIEMPO_ESPERA) as resp:
            return json.loads(resp.read()).get("html_url")
    except Exception as error:  # un aviso que falla no tumba la corrida
        print(f"No pude abrir el aviso de {cambio['nombre']}: {type(error).__name__}: {error}", file=sys.stderr)
        return None


# --- RSS (Atom) ---------------------------------------------------------------

def feed(cambios: list[dict], ahora: datetime) -> str:
    def x(t: str) -> str:
        return html.escape(t, quote=True)

    entradas = []
    for c in cambios[:50]:
        resumen = "\n".join(c["agregadas"][:10]) or "Desaparecieron líneas de la página."
        entradas.append(
            "  <entry>\n"
            f"    <title>{x('Cambió: ' + c['nombre'])}</title>\n"
            f"    <link href=\"{x(c['url'])}\"/>\n"
            f"    <id>tag:estado-x.github.io,2026:{x(c['slug'])}:{x(c['fecha'])}</id>\n"
            f"    <updated>{x(c['fecha'])}</updated>\n"
            f"    <summary>{x(resumen)}</summary>\n"
            "  </entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        "  <title>Alerta de convocatorias · Estado X</title>\n"
        f"  <link href=\"{x(SITIO)}\"/>\n"
        f"  <link rel=\"self\" href=\"{x(SITIO + 'data/feed.xml')}\"/>\n"
        f"  <id>{x(SITIO)}</id>\n"
        f"  <updated>{ahora.isoformat(timespec='seconds')}</updated>\n"
        "  <author><name>Estado X</name></author>\n"
        + "\n".join(entradas)
        + "\n</feed>\n"
    )


# --- revisión -----------------------------------------------------------------

def main() -> int:
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    fuentes = leer_fuentes()
    estado_previo = {f["slug"]: f for f in leer_json(DATOS / "estado.json", {}).get("fuentes", [])}
    cambios = leer_json(DATOS / "cambios.json", {}).get("cambios", [])
    nuevos: list[dict] = []
    errores: list[dict] = []
    estado: list[dict] = []

    for fuente in fuentes:
        previo = estado_previo.get(fuente["slug"], {})
        ficha = {
            "slug": fuente["slug"],
            "nombre": fuente["nombre"],
            "url": fuente["url"],
            "ultima_revision": ahora.isoformat(),
            "ultima_revision_texto": fecha_texto(ahora),
            "ultimo_cambio": previo.get("ultimo_cambio"),
            "ultimo_cambio_texto": previo.get("ultimo_cambio_texto"),
            "vigilada_desde": previo.get("vigilada_desde") or ahora.isoformat(),
            "vigilada_desde_texto": previo.get("vigilada_desde_texto") or fecha_texto(ahora),
            "lineas": previo.get("lineas", 0),
            "estado": "ok",
            "error": None,
            "ultimas_agregadas": previo.get("ultimas_agregadas", []),
        }
        archivo = TEXTOS / f"{fuente['slug']}.txt"
        try:
            lineas = lineas_visibles(descargar(fuente["url"]), fuente["palabras"])
            # Con «palabras», que hoy no haya líneas que coincidan es normal: se guarda vacío
            # y la primera vez que aparezcan sí habrá aviso. Sin «palabras», es un error.
            if not lineas and not fuente["palabras"]:
                raise ValueError("la página no trae texto legible (¿carga todo con JavaScript?)")
        except Exception as error:  # una fuente que falla no frena a las demás
            mensaje = f"{type(error).__name__}: {error}"[:200]
            print(f"{fuente['nombre']}: {mensaje}", file=sys.stderr)
            ficha.update(estado="error", error=mensaje, ultima_revision=previo.get("ultima_revision"),
                         ultima_revision_texto=previo.get("ultima_revision_texto"))
            ficha["intento_fallido"] = ahora.isoformat()
            ficha["intento_fallido_texto"] = fecha_texto(ahora)
            errores.append({"slug": fuente["slug"], "nombre": fuente["nombre"], "error": mensaje})
            estado.append(ficha)
            continue

        anteriores = [l for l in archivo.read_text(encoding="utf-8").splitlines() if l] if archivo.exists() else None
        ficha["lineas"] = len(lineas)
        if anteriores is None:
            ficha["estado"] = "nueva"  # primera revisión: queda como base, sin aviso
        else:
            viejas, nuevas = set(anteriores), set(lineas)
            agregadas = [l for l in lineas if l not in viejas]
            quitadas = [l for l in anteriores if l not in nuevas]
            if agregadas or quitadas:
                cambio = {
                    "slug": fuente["slug"],
                    "nombre": fuente["nombre"],
                    "url": fuente["url"],
                    "fecha": ahora.isoformat(),
                    "fecha_texto": fecha_texto(ahora),
                    "agregadas": agregadas[:40],
                    "agregadas_n": len(agregadas),
                    "quitadas_n": len(quitadas),
                }
                cambio["aviso"] = abrir_aviso(cambio)
                nuevos.append(cambio)
                ficha.update(ultimo_cambio=ahora.isoformat(), ultimo_cambio_texto=fecha_texto(ahora),
                             ultimas_agregadas=agregadas[:8])
        escribir(archivo, "\n".join(lineas) + ("\n" if lineas else ""))
        estado.append(ficha)

    cambios = (nuevos + cambios)[:MAX_CAMBIOS]
    escribir(DATOS / "estado.json", json.dumps({
        "generado": ahora.isoformat(),
        "generado_texto": fecha_texto(ahora),
        "fuentes": estado,
    }, ensure_ascii=False, indent=2) + "\n")
    escribir(DATOS / "cambios.json", json.dumps({"cambios": cambios}, ensure_ascii=False, indent=2) + "\n")
    escribir(DATOS / "corrida.json", json.dumps({
        "fecha": ahora.isoformat(),
        "fecha_texto": fecha_texto(ahora),
        "revisadas": len(fuentes),
        "cambios": len(nuevos),
        "errores": errores,
    }, ensure_ascii=False, indent=2) + "\n")
    escribir(DATOS / "feed.xml", feed(cambios, ahora))
    print(f"Revisadas {len(fuentes)} páginas · {len(nuevos)} con cambios · {len(errores)} con error")
    return 0


if __name__ == "__main__":
    sys.exit(main())
