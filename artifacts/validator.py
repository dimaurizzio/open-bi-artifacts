"""Revisión de la página antes de guardarla.

- errores: reglas forzadas (la escritura se rechaza). El visor además descarta lo
  prohibido al cargar, así que nada depende solo de esta revisión.
- advertencias: convenciones (trazabilidad, tokens de color). No rompen nada,
  pero se le devuelven al agente en cada escritura para frenar el drift.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path

_TOKENS = json.loads((Path(__file__).parent / "tokens.json").read_text(encoding="utf-8"))
_TOKEN_COLORS = {
    c.lower()
    for mode in ("light", "dark")
    for k, v in _TOKENS[mode].items()
    for c in (v if isinstance(v, list) else [v])
    if isinstance(c, str) and c.startswith("#")
} | {c.lower() for c in _TOKENS["sequential"]} | {c.lower() for c in _TOKENS["status"].values()} | {
    c.lower() for c in _TOKENS["diverging"].values()
} | {"#fff", "#ffffff", "#000", "#000000"}

_FORBIDDEN_TAGS = {"iframe", "frame", "frameset", "object", "embed", "form", "base", "meta", "link", "portal", "applet"}
_URL_ATTRS = {"href", "src", "action", "formaction", "xlink:href", "srcset", "poster", "background"}
# Una página que navega a otra dirección se lleva en la URL lo que muestra. Leer
# location.hash (los links dentro de la página) sigue permitido.
_NAVIGATION = (
    r"(?<![\w$])location\s*(?:=(?!=)|\[|\.(?:href|search|pathname|host|hostname|protocol|port)\s*=(?!=)|\.(?:assign|replace|reload)\s*\()"
    r"|\bnavigation\s*\.\s*(?:navigate|reload|traverseTo|back|forward)\b|\bhistory\s*\.\s*(?:go|back|forward)\b|(?<![\w.$])open\s*\(",
    "navegación fuera de la página",
)
_SCRIPT_FORBIDDEN = [
    _NAVIGATION,
    (r"\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|document\.cookie", "almacenamiento del navegador"),
    (r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|\bimport\s*\(", "acceso a red"),
    (r"\beval\s*\(|new\s+Function\s*\(", "eval / new Function"),
    # top./parent. solo como ventana (no margin.top ni node.parent): sin punto ni identificador antes.
    (r"\bwindow\.(open|top|parent|opener)\b|(?<![\w.$])(top|parent)\.|\bpostMessage\b|\.opener\b", "acceso a otras ventanas"),
    (r"\bReact(DOM)?\b|\bRecharts\b|\bChart\.js\b|\bPlotly\b", "librerías no incluidas (solo d3 v7)"),
]
_NUM_TEXT = re.compile(r"(?<![\w#])(?!(?:19|20)\d\d\b)\d[\d.,]*\d%?|\b\d%")


_OWN_FILE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")


class _PageScan(HTMLParser):
    def __init__(self, contract: int = 1):
        super().__init__(convert_charrefs=True)
        self.contract = contract
        self.errors: list[str] = []
        self.unmarked: list[str] = []
        self.sources: list[tuple[str, str | None]] = []
        self.scripts: list[str] = []
        self.styles: list[str] = []
        self.full_document = False
        self.own_files: list[str] = []
        self.row_keys: list[str] = []
        self._stack: list[tuple[str, bool]] = []  # (tag, marked)
        self._in_script = self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("html", "head", "body") or tag == "!doctype":
            self.full_document = True
        own_link = self.contract >= 2 and tag == "link" and _OWN_FILE.match(a.get("href", ""))
        if tag in _FORBIDDEN_TAGS and not own_link:
            self.errors.append(f"Etiqueta no permitida: <{tag}>.")
        if tag == "script":
            if "src" in a and not (self.contract >= 2 and _OWN_FILE.match(a["src"])):
                self.errors.append("Script externo (<script src>): solo se cargan archivos propios (files/<nombre>).")
            elif "src" in a:
                self.own_files.append(a["src"])
            if a.get("type", "").lower() == "module":
                self.errors.append("Módulos ES no disponibles: usá <script> clásico.")
            self._in_script = True
        if tag == "style":
            self._in_style = True
        for k, v in a.items():
            if k.startswith("on"):
                self.errors.append(f"Atributo de evento inline no permitido: {k}= en <{tag}>. Usá addEventListener.")
            if k in _URL_ATTRS and re.match(r"\s*(javascript:|https?:|//|vbscript:)", v, re.I):
                self.errors.append(f"URL externa o javascript: en {k}= de <{tag}>.")
            if k == "style":
                self.styles.append(v)
        if own_link:
            self.own_files.append(a["href"])
        if "data-source" in a:
            if self.contract >= 2:
                ids = a["data-source"].split()
                # Un gráfico o una tabla se marcan sin data-field: no es un valor suelto.
                whole = tag in ("table", "th") or "data-row-key" in a or len(ids) > 1
                for i in ids:
                    self.sources.append((i, a.get("data-field") or ("*" if whole else None)))
                if tag == "table" and "data-row-key" in a:
                    self.row_keys.append(a["data-source"])
            else:
                self.sources.append((a["data-source"], a.get("data-field")))
        marked = "data-source" in a or any(m for _, m in self._stack)
        if tag not in ("br", "img", "input", "hr", "meta", "link", "source", "wbr", "col", "area"):
            self._stack.append((tag, marked))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False
        if tag == "style":
            self._in_style = False
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._in_script:
            self.scripts.append(data)
            return
        if self._in_style:
            self.styles.append(data)
            return
        marked = any(m for _, m in self._stack)
        if not marked:
            for m in _NUM_TEXT.finditer(data):
                self.unmarked.append(data.strip()[:60])
                break

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.full_document = True


def check_page(html: str, dataset_ids: set[str], contract: int = 1) -> dict[str, list[str]]:
    if contract >= 2:
        return _check_page_v2(html, dataset_ids)
    scan = _PageScan()
    scan.feed(html)
    scan.close()
    errors = list(dict.fromkeys(scan.errors))
    warnings: list[str] = []

    script = "\n".join(scan.scripts)
    css = "\n".join(scan.styles)
    for pattern, label in _SCRIPT_FORBIDDEN:
        if re.search(pattern, script):
            errors.append(f"El código usa {label}: no está permitido dentro del visor.")
    if re.search(r"@import|url\(\s*['\"]?\s*(https?:|//)", css, re.I):
        errors.append("CSS externo (@import o url() remota) no permitido.")

    if scan.full_document:
        warnings.append("La página es un fragmento que se monta en #dash-root: no hace falta <html>, <head> ni <body>.")

    calc_ids = set(re.findall(r"dash\.calc\(\s*['\"]([a-z][a-z0-9_]*)['\"]", script))
    known = dataset_ids | calc_ids
    unknown = sorted({s for s, _ in scan.sources if s not in known})
    if unknown:
        warnings.append(f"data-source apunta a fuentes inexistentes: {', '.join(unknown)}.")
    missing_field = sorted({s for s, f in scan.sources if not f})
    if missing_field:
        warnings.append(f"Hay valores con data-source sin data-field ({', '.join(missing_field)}).")
    if scan.unmarked:
        sample = "; ".join(f"'{t}'" for t in scan.unmarked[:4])
        warnings.append(
            f"{len(scan.unmarked)} texto(s) con números fuera de un elemento con data-source"
            f" (ej.: {sample}). Si es un dato, debe salir de un dataset y marcarse."
        )
    colors = set(re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css))
    colors |= set(re.findall(r"['\"](#[0-9a-fA-F]{6})['\"]", script))
    off_token = sorted(c for c in colors if c.lower() not in _TOKEN_COLORS)
    if off_token:
        warnings.append(
            f"Colores fuera de los tokens: {', '.join(off_token[:6])}. Usá var(--dash-*) en CSS o dash.colors en JS."
        )
    if dataset_ids and not re.search(r"dash\.(onData|data)\s*\(", script):
        warnings.append("La página no lee datos (dash.onData / dash.data): los números no van escritos a mano.")
    if re.search(r"viewBox", script + html, re.I):
        warnings.append(
            "La página usa viewBox: el SVG se escala como una imagen y el texto cambia de tamaño al"
            " redimensionar el panel. Dibujá con width/height en píxeles medidos del contenedor en cada"
            " render (el visor vuelve a llamar a dash.onData cuando cambia el ancho)."
        )
    if re.search(r"\.innerHTML\s*[+]?=\s*[^;]*\+", script) or re.search(r"\.html\s*\(\s*[^)]*\+", script):
        warnings.append("innerHTML/.html() con concatenación: insertá valores de datos con textContent/.text().")
    return {"errors": errors, "warnings": warnings}


_SCRIPT_FORBIDDEN_V2 = [
    _NAVIGATION,
    (r"\blocalStorage\b|\bsessionStorage\b|\bindexedDB\b|document\.cookie", "almacenamiento del navegador"),
    (r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts|\bimport\s*\(", "acceso a red"),
    (r"\bwindow\.(open|top|parent|opener)\b|(?<![\w.$])(top|parent)\.|\bpostMessage\b|\.opener\b", "acceso a otras ventanas"),
    (r"\bReact(DOM)?\b|\bRecharts\b|\bChart\.js\b|\bPlotly\b", "librerías no incluidas (solo d3 v7)"),
]


def _check_page_v2(html: str, dataset_ids: set[str]) -> dict[str, list[str]]:
    """Contrato 2: errores = lo que el visor descartaría; advertencias = convenciones de la skill."""
    scan = _PageScan(contract=2)
    scan.feed(html)
    scan.close()
    errors = list(dict.fromkeys(scan.errors))
    warnings: list[str] = []
    script = "\n".join(scan.scripts)
    css = "\n".join(scan.styles)
    for pattern, label in _SCRIPT_FORBIDDEN_V2:
        if re.search(pattern, script):
            errors.append(f"El código usa {label}: la página no lo tiene.")
    if re.search(r"@import|url\(\s*['\"]?\s*(https?:|//)", css, re.I):
        errors.append("CSS externo (@import o url() remota): se descarta.")
    if scan.full_document:
        warnings.append("La página va en #dash-root: sin <html>, <head> ni <body>.")
    calc_ids = set(re.findall(r"dash\.calc\(\s*['\"]([A-Za-z0-9_-]+)['\"]", script))
    clash = sorted(calc_ids & dataset_ids)
    if clash:
        errors.append(f"dash.calc usa un id que ya tiene un dataset: {', '.join(clash)}.")
    unknown = sorted({s for s, _ in scan.sources if s not in dataset_ids | calc_ids})
    if unknown and dataset_ids:
        warnings.append(f"data-source nombra fuentes que no existen: {', '.join(unknown)}.")
    if re.search(r"(?<![\w-])(html|body)\s*[{,]", css):
        warnings.append("El CSS estiliza html o body: restilaría el dashboard alrededor de la página. Usá tus clases.")
    if re.search(r"#dash-root\s*\{(?![^}]*--dash-gutter)[^}]*\}", css):
        warnings.append("La única regla sobre #dash-root es --dash-gutter.")
    hexes = set(re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", css)) | set(re.findall(r"['\"](#[0-9a-fA-F]{6})['\"]", script))
    if hexes:
        warnings.append(f"Colores fijos ({', '.join(sorted(hexes)[:5])}): usá tokens (var(--color-…), var(--ds-…), dash.colors).")
    if re.search(r"var\(--ds-[\w-]*\$\{", script + css):
        warnings.append("Nombres de token armados con ${…}: escribilos completos.")
    if re.search(r"\.innerHTML\s*[+]?=\s*[^;]*\+", script) or re.search(r"\.html\s*\(\s*[^)]*\+", script):
        warnings.append("innerHTML/.html() con concatenación: los valores de datos van con textContent/.text().")
    # Solo cifras con pinta de dato (decimales, miles, %, 4+ dígitos que no son un año): "Últimos 28 días" no es un dato.
    datalike = [t for t in scan.unmarked if re.search(r"\d[.,]\d|\d%|(?<!\d)(?!(?:19|20)\d\d\b)\d{4,}", t)]
    if datalike:
        sample = "; ".join(f"'{t}'" for t in datalike[:3])
        warnings.append(f"Números escritos en el HTML fuera de una marca ({sample}): todo número sale de un dataset.")
    return {"errors": errors, "warnings": warnings}


CHECKLIST = (
    "Checklist del visor: (1) todo número sale de un dataset vía dash.onData/dash.data;"
    " (2) cada valor visible lleva data-source y data-field; (3) colores con var(--dash-*)"
    " o dash.colors; (4) números y fechas con dash.fmt; (5) sin red, storage ni scripts"
    " externos; (6) después de que alguien lo abra, leé las corridas con artifact_runs."
)
