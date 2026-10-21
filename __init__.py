"""Open BI Artifacts: un agente de Google ADK que analiza BigQuery y arma dashboards.

El agente se carga recién cuando alguien lo pide (`agent_dashboards.agent`, como hace
ADK, o el servidor): importar el paquete no lee la configuración, así `open-bi --env …`
puede elegir el entorno antes.
"""

import importlib
from typing import Any


def __getattr__(name: str) -> Any:
    if name == "agent":
        return importlib.import_module(f"{__name__}.agent")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
