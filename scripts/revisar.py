#!/usr/bin/env python3
"""Alerta de convocatorias · revisa las páginas de fuentes.txt y avisa cuando cambian.

Solo usa la biblioteca estándar de Python. Para cada página:
  1. la descarga (forzando IPv4, porque GitHub Actions no alcanza algunos
     servidores que publican IPv6),
  2. saca el texto visible, línea por línea,
  3. lo compara con el texto de la revisión anterior (data/textos/),
  4. si aparecieron o desaparecieron líneas, registra el cambio.

Escribe data/estado.json, data/cambios.json, data/corrida.json, data/feed.xml
y un RSS por tipo (data/feed-<tipo>.xml) cuando las fuentes tienen «tipo:».
Si corre en GitHub Actions con permiso para abrir avisos (issues), abre uno
por cada página donde apareció algo nuevo; quien siga el repo lo recibe por
correo. Si solo desaparecieron líneas, lo anota como «sin novedades», sin
aviso. Los avisos del robot se cierran solos a los 30 días (siguen en la
página y en el RSS).

Listas aparte: `python scripts/revisar.py --lista doctorado` lee
fuentes-doctorado.txt y escribe en data/doctorado/. Si el archivo no existe,
termina sin error.

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


def _lista() -> str | None:
    """Nombre de la lista aparte (--lista doctorado), o None para la lista principal."""
    argumentos = sys.argv[1:]
    if not argumentos:
        return None
    if len(argumentos) == 2 and argumentos[0] == "--lista" and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", argumentos[1]):
        return argumentos[1]
    sys.exit("Uso: python scripts/revisar.py [--lista nombre-en-minusculas]")


LISTA = _lista()
FUENTES = RAIZ / (f"fuentes-{LISTA}.txt" if LISTA else "fuentes.txt")
DATOS = RAIZ / "data" / LISTA if LISTA else RAIZ / "data"
TEXTOS = DATOS / "textos"

BOGOTA = timezone(timedelta(hours=-5), "Bogotá")
AGENTE = "Mozilla/5.0 (compatible; AlertaConvocatorias/1.0; +https://github.com/Estado-X/alerta-convocatorias-colombia)"
TIEMPO_ESPERA = 40      # segundos por cada lectura de la red
TIEMPO_TOTAL = 90       # segundos como máximo para descargar una página completa
TAMANO_MAXIMO = 8_000_000
MAX_CAMBIOS = 300
MAX_LINEAS_AVISO = 20
DIAS_AVISO_ABIERTO = 30  # los avisos del robot se cierran solos después de estos días
CIERRES_POR_CORRIDA = 10  # como mucho, para no mandar una ráfaga de correos el mismo día
CAIDA_MAXIMA = 0.6        # si una página trae más de 60 % menos líneas que la base, no la pisa
LINEAS_MINIMAS_CAIDA = 10  # con bases más chicas no se aplica (una lista filtrada cambia mucho)
CAIDAS_PARA_ACEPTAR = 3   # a la tercera revisión seguida con la caída, se acepta como base nueva
DIAS_VISTAS = 30          # una línea que vuelve antes de estos días no cuenta como nueva
ROBOT = "github-actions[bot]"
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sitio() -> str:
    """Dirección de la página: SITIO_URL si existe; si no, la de GitHub Pages del repo."""
    if os.environ.get("SITIO_URL"):
        return os.environ["SITIO_URL"]
    repo = os.environ.get("GITHUB_REPOSITORY", "Estado-X/alerta-convocatorias-colombia")
    dueno, _, nombre = repo.partition("/")
    return f"https://{dueno.lower()}.github.io/{nombre}/"


SITIO = _sitio()
PAGINA = SITIO + (f"{LISTA}/" if LISTA else "")          # página que muestra esta lista
RUTA_FEED = f"data/{LISTA}/feed.xml" if LISTA else "data/feed.xml"
PREFIJO_AVISO = f"{LISTA.replace('-', ' ').capitalize()} · " if LISTA else ""


def titulo_lista() -> str:
    """Título de la lista: la línea «# Lista: …» de su archivo de fuentes, si la tiene."""
    try:
        for linea in FUENTES.read_text(encoding="utf-8").splitlines():
            encontrado = re.match(r"#\s*Lista:\s*(.+)$", linea.strip())
            if encontrado:
                return encontrado.group(1).strip()
    except FileNotFoundError:
        pass
    return "Alerta de convocatorias"

# Líneas que cambian solas y no dicen nada (contadores, horas de la página, etc.).
RUIDO = [
    re.compile(p, re.I)
    for p in (
        r"^hace \d+ (segundos?|minutos?|horas?|d[ií]as?)$",
        r"^\d+ (visitas|vistas|views)$",
        r"^(visitas|vistas|views)\s*:?\s*\d+",
        r"(última|ultima) actualizaci[oó]n",
        r"^fecha de actualizaci[oó]n\b",
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


def partes_opcionales(partes: list[str]) -> tuple[list[str], list[str]]:
    """Lo que va después de la dirección: «palabras» y, si está, «tipo: Becas, Cooperación».

    El tipo es opcional y puede ir en cualquier columna después de la dirección, así que
    las listas hechas antes de que existiera siguen sirviendo tal como están.
    """
    palabras: list[str] = []
    tipos: list[str] = []
    for parte in partes:
        encontrado = re.match(r"\s*tipos?\s*:\s*(.*)$", parte, re.I)
        if encontrado:
            for tipo in encontrado.group(1).split(","):
                tipo = re.sub(r"\s+", " ", tipo).strip()[:40]
                if tipo and slug(tipo) not in {slug(t) for t in tipos}:
                    tipos.append(tipo)
        else:
            palabras.extend(sin_tildes(p.strip()) for p in parte.split(",") if p.strip())
    return palabras, tipos


def leer_fuentes() -> list[dict]:
    fuentes = []
    usados: set[str] = set()
    for numero, linea in enumerate(FUENTES.read_text(encoding="utf-8").splitlines(), 1):
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        partes = [p.strip() for p in linea.split("|")]
        if len(partes) < 2 or not partes[1].startswith(("http://", "https://")):
            print(f"{FUENTES.name}, línea {numero}: no la entiendo, la salto ({linea[:60]})", file=sys.stderr)
            continue
        palabras, tipos = partes_opcionales(partes[2:])
        base = slug(partes[0])
        nombre_archivo, n = base, 2
        while nombre_archivo in usados:  # dos nombres parecidos no se pisan el archivo
            nombre_archivo = f"{base}-{n}"
            n += 1
        if nombre_archivo != base:
            print(f"{FUENTES.name}, línea {numero}: el nombre se parece a otro; lo guardo como {nombre_archivo}", file=sys.stderr)
        usados.add(nombre_archivo)
        fuentes.append({"nombre": partes[0], "url": partes[1], "palabras": palabras, "tipos": tipos,
                        "slug": nombre_archivo})
    return fuentes


def comparar(anteriores: list[str], lineas: list[str]) -> tuple[list[str], list[str]]:
    """Líneas que aparecieron y líneas que ya no están, en el orden de cada página."""
    viejas, nuevas = set(anteriores), set(lineas)
    return [l for l in lineas if l not in viejas], [l for l in anteriores if l not in nuevas]


def parece_caida(anteriores: list[str], lineas: list[str]) -> bool:
    """¿La página trajo muchas menos líneas que la base? Suele ser una página de error o de
    mantenimiento que responde con código 200; pisar la base daría un aviso falso al día siguiente."""
    return len(anteriores) >= LINEAS_MINIMAS_CAIDA and len(lineas) < len(anteriores) * (1 - CAIDA_MAXIMA)


def clave_linea(linea: str) -> str:
    return hashlib.sha1(linea.encode("utf-8")).hexdigest()[:16]


def leer_vistas(ruta: Path, ahora: datetime) -> dict[str, str]:
    """Líneas vistas en los últimos DIAS_VISTAS días: {clave: fecha en que se vio por última vez}."""
    limite = (ahora - timedelta(days=DIAS_VISTAS)).date().isoformat()
    datos = leer_json(ruta, {})
    if not isinstance(datos, dict):
        return {}
    return {k: v for k, v in datos.items() if isinstance(v, str) and v >= limite}


def actualizar_vistas(vistas: dict[str, str], lineas: list[str], ahora: datetime) -> dict[str, str]:
    hoy = ahora.date().isoformat()
    salida = dict(vistas)
    for linea in lineas:
        salida[clave_linea(linea)] = hoy
    return dict(sorted(salida.items()))


def sin_novedades(cambio: dict) -> bool:
    """Un cambio en el que solo desaparecieron líneas: no trae nada nuevo que leer."""
    agregadas = cambio.get("agregadas_n")
    if not isinstance(agregadas, int):
        agregadas = len(cambio.get("agregadas") or [])
    return agregadas == 0


# --- avisos en GitHub ---------------------------------------------------------

def _github() -> tuple[str, str] | None:
    """Token y repo si esta corrida puede usar los avisos de GitHub; si no, None."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo or os.environ.get("AVISOS_GITHUB", "si").lower() in ("no", "0", "false"):
        return None
    return token, repo


def _api(token: str, url: str, metodo: str = "GET", cuerpo: dict | None = None):
    pedido = urllib.request.Request(
        url,
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        method=metodo,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": AGENTE,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(pedido, timeout=TIEMPO_ESPERA) as resp:
        return json.loads(resp.read() or b"null")


def etiquetas_aviso() -> list[str]:
    """«aviso» en todos los avisos del robot, más el nombre de la lista si es una lista aparte.

    Así los avisos no se mezclan con los issues de quienes colaboran.
    """
    return ["aviso"] + ([LISTA] if LISTA else [])


def cuerpo_aviso(cambio: dict) -> str:
    # El texto viene de páginas de terceros: va dentro de un bloque de código para que
    # GitHub no lo convierta en menciones (@cuenta), referencias (repo#1) ni enlaces.
    seguras = [l.replace("`", "'") for l in cambio["agregadas"][:MAX_LINEAS_AVISO]]
    lineas = "```text\n" + "\n".join(seguras) + "\n```"
    mas = cambio["agregadas_n"] - min(cambio["agregadas_n"], MAX_LINEAS_AVISO)
    return (
        f"**{cambio['nombre']}** cambió (revisión del {cambio['fecha_texto']}).\n\n"
        f"Página: {cambio['url']}\n\n"
        f"Líneas nuevas ({cambio['agregadas_n']}):\n\n{lineas}\n"
        + (f"\n…y {mas} más.\n" if mas > 0 else "")
        + f"\nLíneas que ya no están: {cambio['quitadas_n']}.\n\n"
        f"Abre la página oficial para leer la convocatoria completa: este aviso solo dice que algo cambió.\n\n"
        f"Todas las páginas: {PAGINA}\n\n"
        f"Este aviso se cierra solo a los {DIAS_AVISO_ABIERTO} días; el cambio sigue en la página y en el RSS."
    )


def abrir_aviso(cambio: dict) -> str | None:
    if sin_novedades(cambio):  # solo desaparecieron líneas: no hay nada nuevo que avisar
        return None
    github = _github()
    if not github:
        return None
    token, repo = github
    titulo = f"{PREFIJO_AVISO}Cambió: {cambio['nombre']} · {cambio['fecha_texto'][:10]}"
    cuerpo = cuerpo_aviso(cambio)
    url = f"https://api.github.com/repos/{repo}/issues"
    try:
        try:
            respuesta = _api(token, url, "POST", {"title": titulo, "body": cuerpo, "labels": etiquetas_aviso()})
        except urllib.error.HTTPError as error:
            if error.code not in (403, 422):
                raise
            # Si las etiquetas no se pueden poner, el aviso sale igual, sin etiquetas.
            respuesta = _api(token, url, "POST", {"title": titulo, "body": cuerpo})
        return (respuesta or {}).get("html_url")
    except Exception as error:  # un aviso que falla no tumba la corrida
        print(f"No pude abrir el aviso de {cambio['nombre']}: {type(error).__name__}: {error}", file=sys.stderr)
        return None


def avisos_por_cerrar(issues: list[dict], ahora: datetime, dias: int = DIAS_AVISO_ABIERTO) -> list[dict]:
    """Avisos abiertos de esta lista, abiertos por el robot hace más de «dias» días.

    Nunca toca issues de personas ni pull requests: solo los del robot cuyo título
    empieza como los avisos de esta lista («Cambió: …» o «Doctorado · Cambió: …»).
    """
    if dias <= 0:
        return []
    limite = ahora - timedelta(days=dias)
    prefijo = f"{PREFIJO_AVISO}Cambió: "
    salida = []
    for issue in issues:
        if "pull_request" in issue or issue.get("state", "open") != "open":
            continue
        if (issue.get("user") or {}).get("login") != ROBOT:
            continue
        if not str(issue.get("title", "")).startswith(prefijo):
            continue
        try:
            creado = datetime.fromisoformat(str(issue["created_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if creado <= limite:
            salida.append((creado, issue))
    return [issue for _, issue in sorted(salida, key=lambda par: par[0])]  # los más viejos primero


def dias_aviso_abierto() -> int:
    try:
        return int(os.environ.get("AVISOS_DIAS", DIAS_AVISO_ABIERTO))
    except ValueError:
        return DIAS_AVISO_ABIERTO


def cerrar_avisos_viejos(ahora: datetime) -> int:
    """Cierra los avisos del robot que llevan más de 30 días abiertos (AVISOS_DIAS lo cambia; 0 no cierra)."""
    github = _github()
    dias = dias_aviso_abierto()
    if not github or dias <= 0:
        return 0
    token, repo = github
    abiertos: list[dict] = []
    try:
        for pagina in range(1, 11):  # hasta 1.000 issues abiertos
            lote = _api(token, f"https://api.github.com/repos/{repo}/issues?state=open&sort=created"
                               f"&direction=asc&per_page=100&page={pagina}")
            if not lote:
                break
            abiertos.extend(lote)
            if len(lote) < 100:
                break
    except Exception as error:
        print(f"No pude leer los avisos abiertos: {type(error).__name__}: {error}", file=sys.stderr)
        return 0
    cerrados = 0
    for issue in avisos_por_cerrar(abiertos, ahora, dias)[:CIERRES_POR_CORRIDA]:
        numero = issue.get("number")
        try:
            _api(token, f"https://api.github.com/repos/{repo}/issues/{numero}/comments", "POST", {
                "body": f"Este aviso se cierra solo a los {dias} días. El cambio sigue en la página "
                        f"y en el RSS: {PAGINA}"})
            _api(token, f"https://api.github.com/repos/{repo}/issues/{numero}", "PATCH",
                 {"state": "closed", "state_reason": "completed"})
            cerrados += 1
        except Exception as error:  # un aviso que no se cierra no tumba la corrida
            print(f"No pude cerrar el aviso #{numero}: {type(error).__name__}: {error}", file=sys.stderr)
    return cerrados


# --- RSS (Atom) ---------------------------------------------------------------

def ruta_feed_tipo(tipo: str) -> str:
    """data/feed-becas.xml (o data/doctorado/feed-en-el-exterior.xml en una lista aparte)."""
    return (f"data/{LISTA}/" if LISTA else "data/") + f"feed-{slug(tipo)}.xml"


def tipos_de(cambio: dict, tipos_por_slug: dict[str, list[str]]) -> list[str]:
    """Tipos de un cambio: los que guardó él o, si es anterior a los tipos, los de su fuente hoy."""
    if cambio.get("tipos"):
        return list(cambio["tipos"])
    return list(tipos_por_slug.get(cambio.get("slug", ""), []))


def tipos_en_orden(fuentes: list[dict]) -> list[str]:
    """Tipos en el orden de las páginas, sin repetir («Becas» y «becas» son el mismo)."""
    vistos: list[str] = []
    claves: set[str] = set()
    for f in fuentes:
        for t in f.get("tipos", []):
            if slug(t) not in claves:
                claves.add(slug(t))
                vistos.append(t)
    return vistos


def es_del_tipo(cambio: dict, tipo: str, tipos_por_slug: dict[str, list[str]]) -> bool:
    return slug(tipo) in {slug(t) for t in tipos_de(cambio, tipos_por_slug)}


def feed(cambios: list[dict], ahora: datetime, tipo: str | None = None) -> str:
    """RSS (Atom) con lo nuevo. Los cambios «sin novedades» no entran: no traen nada que leer."""
    def x(t: str) -> str:
        return html.escape(t, quote=True)

    titulo = titulo_lista() + (f" · {tipo}" if tipo else "")
    propio = SITIO + (ruta_feed_tipo(tipo) if tipo else RUTA_FEED)
    entradas = []
    for c in [c for c in cambios if not sin_novedades(c)][:50]:
        resumen = "\n".join((c.get("agregadas") or [])[:10])
        entradas.append(
            "  <entry>\n"
            f"    <title>{x('Cambió: ' + c['nombre'])}</title>\n"
            f"    <link href=\"{x(c['url'])}\"/>\n"
            f"    <id>tag:estado-x.github.io,2026:{x((LISTA + ':') if LISTA else '')}{x(c['slug'])}:{x(c['fecha'])}</id>\n"
            f"    <updated>{x(c['fecha'])}</updated>\n"
            f"    <summary>{x(resumen)}</summary>\n"
            "  </entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{x(titulo)} · Estado X</title>\n"
        f"  <link href=\"{x(PAGINA)}\"/>\n"
        f"  <link rel=\"self\" href=\"{x(propio)}\"/>\n"
        f"  <id>{x(propio if tipo else PAGINA)}</id>\n"
        f"  <updated>{ahora.isoformat(timespec='seconds')}</updated>\n"
        "  <author><name>Estado X</name></author>\n"
        + "\n".join(entradas)
        + "\n</feed>\n"
    )


# --- revisión -----------------------------------------------------------------

def main() -> int:
    ahora = datetime.now(timezone.utc).replace(microsecond=0)
    if not FUENTES.exists():
        print(f"No encontré {FUENTES.name}; no hay nada que revisar en esta lista.")
        return 0
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
            "tipos": fuente["tipos"],
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
            "caidas_seguidas": 0,
        }
        archivo = TEXTOS / f"{fuente['slug']}.txt"
        archivo_vistas = TEXTOS / f"{fuente['slug']}.vistas.json"
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
        if anteriores is not None and parece_caida(anteriores, lineas):
            seguidas = int(previo.get("caidas_seguidas") or 0) + 1
            if seguidas < CAIDAS_PARA_ACEPTAR:
                # Se conserva la base y la página queda «con error» hasta que vuelva a ser la de antes.
                mensaje = (f"la página trajo {len(lineas)} líneas de {len(anteriores)}: parece una página "
                           f"de error o de mantenimiento; se conserva la revisión anterior")
                print(f"{fuente['nombre']}: {mensaje}", file=sys.stderr)
                ficha.update(estado="error", error=mensaje, caidas_seguidas=seguidas,
                             ultima_revision=previo.get("ultima_revision"),
                             ultima_revision_texto=previo.get("ultima_revision_texto"),
                             intento_fallido=ahora.isoformat(), intento_fallido_texto=fecha_texto(ahora))
                errores.append({"slug": fuente["slug"], "nombre": fuente["nombre"], "error": mensaje})
                estado.append(ficha)
                continue
            # Tercera revisión seguida igual: la página de verdad cambió así. Se acepta como base nueva.
        vistas = leer_vistas(archivo_vistas, ahora)
        ficha["lineas"] = len(lineas)
        if anteriores is None:
            ficha["estado"] = "nueva"  # primera revisión: queda como base, sin aviso
        else:
            agregadas, quitadas = comparar(anteriores, lineas)
            # Una línea que ya se vio en los últimos 30 días y vuelve no es nueva.
            agregadas = [l for l in agregadas if clave_linea(l) not in vistas]
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
                    "tipos": fuente["tipos"],
                    "sin_novedades": not agregadas,
                }
                # Si solo desaparecieron líneas, queda anotado como «sin novedades»:
                # sin aviso, fuera del RSS y sin mover el «último cambio» de la página.
                cambio["aviso"] = abrir_aviso(cambio)
                nuevos.append(cambio)
                if agregadas:
                    ficha.update(ultimo_cambio=ahora.isoformat(), ultimo_cambio_texto=fecha_texto(ahora),
                                 ultimas_agregadas=agregadas[:8])
        escribir(archivo, "\n".join(lineas) + ("\n" if lineas else ""))
        escribir(archivo_vistas, json.dumps(actualizar_vistas(vistas, lineas, ahora), indent=0) + "\n")
        estado.append(ficha)

    cambios = (nuevos + cambios)[:MAX_CAMBIOS]
    tipos = tipos_en_orden(fuentes)
    tipos_por_slug = {f["slug"]: f["tipos"] for f in fuentes}
    con_novedades = sum(1 for c in nuevos if not sin_novedades(c))
    escribir(DATOS / "estado.json", json.dumps({
        "generado": ahora.isoformat(),
        "generado_texto": fecha_texto(ahora),
        "fuentes": estado,
        "tipos": [{"nombre": t, "feed": ruta_feed_tipo(t)} for t in tipos],
    }, ensure_ascii=False, indent=2) + "\n")
    escribir(DATOS / "cambios.json", json.dumps({"cambios": cambios}, ensure_ascii=False, indent=2) + "\n")
    escribir(DATOS / "corrida.json", json.dumps({
        "fecha": ahora.isoformat(),
        "fecha_texto": fecha_texto(ahora),
        "revisadas": len(fuentes),
        "cambios": len(nuevos),
        "con_novedades": con_novedades,
        "errores": errores,
    }, ensure_ascii=False, indent=2) + "\n")
    escribir(DATOS / "feed.xml", feed(cambios, ahora))
    for tipo in tipos:
        del_tipo = [c for c in cambios if es_del_tipo(c, tipo, tipos_por_slug)]
        escribir(RAIZ / ruta_feed_tipo(tipo), feed(del_tipo, ahora, tipo))
    cerrados = cerrar_avisos_viejos(ahora)
    print(f"Revisadas {len(fuentes)} páginas · {con_novedades} con algo nuevo · "
          f"{len(nuevos) - con_novedades} sin novedades · {len(errores)} con error"
          + (f" · {cerrados} avisos viejos cerrados" if cerrados else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
