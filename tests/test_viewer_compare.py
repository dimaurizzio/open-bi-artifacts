"""El comparador del visor (sameValue en viewer-v2.js): qué cuenta como el mismo valor.

Corre las funciones reales del visor en un navegador headless. Se saltea si no está
Playwright con Chromium (`pip install playwright` y `python -m playwright install chromium`).
"""

import pathlib

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

STATIC = pathlib.Path(__file__).resolve().parents[1] / "server" / "static"

CASES = [
    # Fechas: formatos de una app en castellano y en inglés.
    ("18/09", "2026-09-18", True),
    ("7/10", "2026-10-07", True),
    ("18 sep", "2026-09-18", True),
    ("18 sept. 2026", "2026-09-18", True),
    ("18 de septiembre", "2026-09-18", True),
    ("septiembre de 2026", "2026-09-01", True),
    ("Sep 18", "2026-09-18", True),
    ("19/09", "2026-09-18", False),
    # Porcentajes y puntos porcentuales contra una fracción.
    ("+14,0%", 0.14, True),
    ("-2,2%", -0.022, True),
    ("+0,4 pp", 0.0036169, True),
    ("+0,4 p.p.", 0.0036, True),
    ("+0,9 pp", 0.0036, False),
    # Abreviaturas y redondeo.
    ("USD 5,5 M", 5_530_000, True),
    ("27,5 k", 27_500, True),
    ("USD 201", 201.4, True),
    ("USD 210", 201.4, False),
]


@pytest.fixture(scope="module")
def same_value():
    js = (STATIC / "viewer-v2.js").read_text(encoding="utf-8")
    chunk = js[js.index("  const PLACEHOLDER ="):js.index("  function describeMark")]
    d3 = (STATIC / "vendor" / "d3.v7.min.js").read_text(encoding="utf-8")
    with sync_api.sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as e:  # noqa: BLE001 - sin Chromium instalado
            pytest.skip(f"Chromium de Playwright no disponible: {e}")
        page = browser.new_page()
        page.add_script_tag(content=d3)
        page.add_script_tag(content=f"window.__sameValue = (function () {{\n{chunk}\nreturn sameValue;\n}})();")
        yield lambda shown, value: page.evaluate("([s, v]) => window.__sameValue(s, v)", [shown, value])
        browser.close()


@pytest.mark.parametrize(("shown", "value", "expected"), CASES)
def test_same_value(same_value, shown, value, expected):
    assert same_value(shown, value) is expected
