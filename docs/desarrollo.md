# Desarrollo

Cómo está organizado el código y cómo extenderlo. Para levantar el entorno, seguí
[primeros-pasos.md](primeros-pasos.md); para el diseño completo,
[arquitectura.md](arquitectura.md).

## Estructura

```
open-bi-artifacts/       El paquete de Python agent_dashboards (pip install -e .)
├── cli.py               Comando open-bi: serve, demo, backup, migrate
├── agent.py             Agente raíz: instrucciones, skills, herramientas, callbacks
├── models.py            Modelo y esfuerzo por turno, de cualquier proveedor
├── config.py            Carga .env.<APP_ENV> y valida la configuración
├── routing.py           Routing de modelo por skill (desactivado)
├── user_context.py      Nombre y momento del día de quien chatea
├── skills/              Skills del agente (SKILL.md + references/)
├── warehouse/           Cliente MCP de BigQuery, guardas SQL, credenciales
├── artifacts/           Dashboards: almacén, validador, herramientas del agente
├── knowledge/           Base de conocimiento y exportación OKF
├── auth/                Login con Google, sesiones, proyectos, permisos
├── server/              FastAPI: chat, APIs, visor, retención, backups
│   └── static/          Visor de dashboards y build del front
├── web/                 Front: React + TypeScript + Vite
│   └── src/
│       ├── components/assistant-ui/   Componentes de assistant-ui (registry)
│       ├── components/ui/             Componentes de shadcn/ui
│       ├── components/app/            Paneles propios: dashboards, conocimiento, cuenta
│       └── lib/                       Store, API, modelos, contexto de vista
├── demo/                Seed de BigQuery y conocimiento de la demo
├── tests/               Tests del backend (pytest)
└── docs/                Esta documentación
```

## Tests y chequeos

Desde la carpeta del repo:

```bash
pytest
```

```bash
cd web && npm run typecheck && npm run build
```

El CI (`.github/workflows/ci.yml`) corre lo mismo y construye la imagen de Docker. Los
tests no llaman a BigQuery ni a ningún modelo: usan dobles y bases temporales.

`tests/test_viewer_compare.py` prueba el comparador del visor (qué valor mostrado
coincide con qué dato: fechas, porcentajes, abreviaturas) en un Chromium headless. Se
saltea si no está instalado:

```bash
python -m playwright install chromium
```

## Cómo hacer…

### Sumar un modelo o un proveedor

No hace falta tocar código. Sumalo a `AGENT_MODELS` con el nombre de LiteLLM
(`proveedor/modelo`) y configurá la clave del proveedor. El esfuerzo que ofrece el chat
sale del mapa de modelos de LiteLLM. Si el proveedor necesita una clave que la app no
conoce, sumala a `_MODEL_KEYS` en `config.py` para que el arranque la valide.

### Cambiar lo que sabe hacer el agente

- **Reglas generales** (tono, cómo citar, qué no hacer): en `INSTRUCTION`, en
  `agent.py`.
- **Cómo consultar el warehouse:** `skills/bigquery-warehouse/SKILL.md`.
- **Cómo armar dashboards:** `skills/dashboard/SKILL.md` y sus `references/`.

Las skills no llevan valores fijos: lo que depende de la instalación (proyecto,
datasets, límites) llega como variables del estado, como `{wh_project}`. Si escribís
llaves literales en una skill con `adk_inject_state`, ADK las va a interpretar como
variables.

### Sumar una skill

1. Creá `skills/<nombre>/SKILL.md` con su frontmatter (`name`, `description`) y, si
   hace falta, `references/`.
2. Sumá el nombre a `SKILL_NAMES` en `agent.py`.
3. Si la skill necesita herramientas propias, sumalas a `tools` del agente desde el
   arranque, no con `adk_additional_tools`: la lista de herramientas tiene que ser la
   misma en toda la conversación (ver [arquitectura](arquitectura.md#un-prompt-que-no-cambia)).
   Si solo deben usarse con la skill cargada, agregá una guarda como
   `require_dashboard_skill` en `artifacts/dashboard_tools.py`.
4. Lo que cambia en cada turno no va en la instrucción: mandalo delante del mensaje,
   como la hora local (`turn_context` en `user_context.py`).

### Sumar una herramienta

Una función de Python con docstring y tipos, que recibe `tool_context: ToolContext`
si necesita saber quién chatea. Ejemplos en `knowledge/tools.py` y
`artifacts/dashboard_tools.py`. Si la herramienta toca dashboards, usá los métodos de
`DashboardService`: ahí se fuerzan los permisos.

### Cambiar el front

- Los componentes de `web/src/components/assistant-ui/` y `web/src/components/ui/`
  vienen de los registries de assistant-ui y shadcn/ui: son código nuestro y se editan
  directo. Para sumar uno nuevo, `npx shadcn@latest add <componente>`.
- El estado de la app está en `web/src/lib/store.ts`. assistant-ui solo dibuja y llama
  a sus acciones (`ExternalStoreRuntime`).
- El visor de dashboards (`server/static/viewer-v2.js` y `viewer-v2.css`) no usa React:
  es JS sin módulos que corre dentro del iframe aislado.

### Cambiar la demo

`demo/seed.py` genera los datos con SQL determinístico (`FARM_FINGERPRINT`), así que
cada corrida da los mismos números para la misma fecha. `demo/knowledge.py` carga el
conocimiento que la explica. `tests/test_demo.py` valida el SQL sin correrlo.

## Convenciones

- Textos de la interfaz, mensajes, comentarios y documentación en español rioplatense,
  claros y breves.
- Seguí el estilo del archivo que tocás.
- Sumá o ajustá tests para lo que cambiás.
- Nunca subas `.env.*` (salvo las plantillas), datos de `data/` ni credenciales.

Ver también [CONTRIBUTING.md](../CONTRIBUTING.md).
