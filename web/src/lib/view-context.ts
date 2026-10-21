/**
 * Contexto de vista: dónde está la persona en el dashboard abierto al lado del chat.
 *
 * Lo actualiza el panel del dashboard y viaja delante de cada mensaje (el servidor lo
 * envuelve en <view-context>). Nunca lleva valores: ids de fuentes, nombres de filtros,
 * el lugar (`dash.setLink`), qué fuentes no cargaron y cuántos problemas reportó la página.
 */
export interface ViewContext {
  dashboard: string;
  mode: 'page' | 'data';
  place?: string;
  selected?: string[];
  at?: string;
  field?: string;
  filters?: string[];
  failed?: number;
  failures?: { id: string; label: string }[];
  /** Números marcados que no coinciden con sus datos, según las corridas de la página. */
  flagged?: number;
  /** Errores del código de la página, según las corridas. */
  errors?: number;
}

let current: ViewContext | null = null;

export function setViewContext(ctx: ViewContext | null) {
  current = ctx;
}

export function currentViewContext(): ViewContext | null {
  return current;
}
