import pytest

from agent_dashboards import config
from agent_dashboards.warehouse import sql_guard as g


@pytest.fixture(autouse=True)
def warehouse(monkeypatch):
    monkeypatch.setattr(config, "WAREHOUSE_PROJECT", "mi-proyecto")
    monkeypatch.setattr(config, "WAREHOUSE_DATASETS", ["mi-proyecto.analytics"])


T ="`mi-proyecto.analytics.ventas_trimestre`"


@pytest.mark.parametrize(
    "sql",
    [
        f"SELECT * FROM {T} t WHERE EXTRACT(YEAR FROM t.fecha) = 2026",
        "WITH a AS (SELECT 1 x FROM analytics.T) SELECT * FROM a JOIN a b ON a.x IS DISTINCT FROM b.x",
        f"SELECT * FROM {T} t CROSS JOIN t.items WHERE s = {{{{seccion}}}} AND f >= {{{{desde}}}};",
        "SELECT table_name FROM `mi-proyecto.analytics.INFORMATION_SCHEMA.TABLES`",
        f"SELECT 'DELETE; DROP' AS txt, x -- UPDATE en comentario\nFROM {T}",
    ],
)
def test_allowed(sql):
    g.check_query(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM x.y.z WHERE 1=1",
        f"SELECT 1 FROM {T}; DROP TABLE a.b",
        "SELECT * FROM `mi-proyecto.OTRO.T`",
        f"SELECT * FROM {T} WHERE name LIKE '%{{{{q}}}}%'",
        "SELECT * FROM OTRO.T",
        "SELECT * FROM `region-us`.INFORMATION_SCHEMA.JOBS",
        f"SELECT * FROM {T} a, `otro-proyecto.d.e` b",
        "SELECT * FROM `{{tabla}}`",
    ],
)
def test_rejected(sql):
    with pytest.raises(g.SqlGuardError):
        g.check_query(sql)


def test_params_render_as_single_literals():
    sql = f"SELECT * FROM {T} WHERE s = {{{{seccion}}}} AND f >= {{{{desde}}}} AND x IN UNNEST({{{{tags}}}})"
    specs = {"seccion": {"type": "text"}, "desde": {"type": "date", "default": "2026-01-01"}, "tags": {"type": "multi"}}
    out = g.render(sql, specs, {"seccion": "O'Brien\\", "tags": ["a", "b"]})
    assert "s = 'O\\'Brien\\\\'" in out
    assert "f >= DATE '2026-01-01'" in out
    assert "UNNEST(['a', 'b'])" in out


def test_undeclared_param_rejected():
    with pytest.raises(g.SqlGuardError):
        g.render("SELECT {{x}}", {}, {})


@pytest.mark.parametrize("value,typ", [("2026-13-01", "date"), ("1; DROP", "number"), ("nan", "number")])
def test_bad_literals(value, typ):
    with pytest.raises((g.SqlGuardError, ValueError)):
        g.literal(value, typ)


# Un alias solo vale donde BigQuery lo ve: no puede esconder una tabla de otro dataset.
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM `mi-proyecto.secreto.sueldos` CROSS JOIN (SELECT 1 FROM `mi-proyecto.analytics.ventas` AS `mi-proyecto` LIMIT 1)",
        "SELECT * FROM `mi-proyecto.secreto.sueldos` CROSS JOIN (SELECT 1 FROM `mi-proyecto.analytics.ventas` AS proy LIMIT 1) AS x",
        "WITH secreto AS (SELECT 1 AS x) SELECT * FROM secreto.sueldos CROSS JOIN secreto",
        "SELECT * FROM secreto.sueldos CROSS JOIN (SELECT 1 FROM `mi-proyecto.analytics.ventas` AS secreto LIMIT 1)",
        "SELECT * FROM `mi-proyecto.analytics.t` x, `secreto.sueldos` JOIN `mi-proyecto.analytics.u` AS secreto ON TRUE",
    ],
)
def test_alias_out_of_scope_does_not_hide_a_table(sql):
    with pytest.raises(g.SqlGuardError):
        g.check_datasets(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT p.total, p.dir.ciudad FROM `mi-proyecto.analytics.pedidos` p",
        "SELECT i FROM `mi-proyecto.analytics.pedidos` AS t JOIN t.items AS i",
        "WITH v AS (SELECT * FROM `mi-proyecto.analytics.ventas`) SELECT v.a.b FROM v",
        "SELECT ARRAY(SELECT x FROM UNNEST(t.items) x) FROM `mi-proyecto.analytics.pedidos` t",
    ],
)
def test_alias_paths_in_scope_are_not_tables(sql):
    g.check_datasets(sql)
