# Cómo contribuir

¡Gracias por sumarte! Issues y pull requests son bienvenidos.

## Antes de empezar

- Para algo grande, abrí primero un issue con la idea: así acordamos el enfoque
  antes de que inviertas tiempo.
- El diseño está en [docs/arquitectura.md](docs/arquitectura.md). Si tu cambio lo
  modifica, actualizá la documentación en el mismo PR.

## Entorno

Seguí la instalación local del [README](README.md). Para probar sin datos reales,
usá la demo de [docs/demo.md](docs/demo.md). Cómo está organizado el código y
cómo extenderlo: [docs/desarrollo.md](docs/desarrollo.md).

## Antes de abrir el PR

```bash
pytest
```

```bash
cd web && npm run typecheck && npm run build
```

- Sumá o ajustá tests para lo que cambiaste.
- Seguí el estilo del código que tocás. Los textos y comentarios van en español
  rioplatense, claros y breves.
- Nunca subas `.env.*` (salvo las plantillas), datos de `data/` ni credenciales.

## Seguridad

Si encontrás una vulnerabilidad, no abras un issue público: seguí
[SECURITY.md](SECURITY.md).
