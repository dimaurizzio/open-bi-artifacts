import { create } from 'zustand';

/**
 * Modelo y esfuerzo que elige la persona en el chat.
 *
 * El catálogo lo arma el servidor (GET /api/models, desde AGENT_MODELS) con el
 * esfuerzo que acepta cada modelo, sea del proveedor que sea. La elección se
 * recuerda en este navegador y viaja con cada mensaje; el servidor la valida.
 */
export interface ModelCatalog {
  default: string;
  models: { id: string; name: string; efforts: { id: string; name: string }[] }[];
}

interface Choice {
  model?: string;
  effort?: string;
}

const STORAGE_KEY = 'ad-model-choice';

function saved(): Choice {
  try {
    const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    return { model: value.model || undefined, effort: value.effort || undefined };
  } catch {
    return {};
  }
}

function save(choice: Choice) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choice));
  } catch {
    // Sin almacenamiento (ventana privada): la elección dura lo que la pestaña.
  }
}

interface ModelState extends Choice {
  catalog: ModelCatalog | null;
  load: () => Promise<void>;
  setModel: (model: string) => void;
  setEffort: (effort: string) => void;
}

export const useModels = create<ModelState>((set, get) => ({
  catalog: null,
  ...saved(),

  async load() {
    const res = await fetch('/api/models').catch(() => null);
    if (!res?.ok) return;
    const catalog = (await res.json()) as ModelCatalog;
    const known = (id?: string) => catalog.models.some((m) => m.id === id);
    set((s) => ({ catalog, model: known(s.model) ? s.model : catalog.default }));
  },

  setModel(model) {
    set({ model });
    save({ model, effort: get().effort });
  },

  setEffort(effort) {
    set({ effort });
    save({ model: get().model, effort });
  },
}));

/** Lo que viaja con cada mensaje: un esfuerzo que el modelo no acepta, el servidor lo ignora. */
export function modelChoice(): Choice {
  const { model, effort } = useModels.getState();
  return { model, effort };
}
