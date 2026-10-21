from pathlib import Path

from agent_dashboards.artifacts.validator import check_page

# Contrato 1 (artifacts viejos): su plantilla quedó en tests/fixtures.
TEMPLATE = (Path(__file__).parent / "fixtures" / "plantilla_contrato1.html").read_text(encoding="utf-8")


def test_template_is_clean():
    result = check_page(TEMPLATE, {"totales", "serie"})
    assert result["errors"] == []
    assert result["warnings"] == []


def test_viewbox_is_flagged():
    page = TEMPLATE.replace(
        ".attr('width', width).attr('height', height)",
        ".attr('viewBox', `0 0 ${width} ${height}`)",
    )
    assert page != TEMPLATE
    warnings = check_page(page, {"totales", "serie"})["warnings"]
    assert any("viewBox" in w for w in warnings)


def test_forbidden_page_is_rejected():
    errors = check_page('<script src="https://x.com/a.js"></script><div onclick="x()">1</div>', set())["errors"]
    assert any("externo" in e for e in errors) and any("onclick" in e for e in errors)


def _errors(code: str) -> list[str]:
    return check_page(f"<script>{code}</script>", set())["errors"]


def test_margin_top_is_not_window_access():
    assert _errors("const m = {top: 12}; const y = m.top + 5; const p = node.parent.id; svg.attr('y', margin.top.toFixed(0));") == []


def test_window_access_is_rejected():
    for code in ("top.location = 'x'", "parent.postMessage({}, '*')", "window.top.document", "window.open('x')"):
        assert any("otras ventanas" in e for e in _errors(code)), code
