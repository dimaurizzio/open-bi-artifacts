import { create } from 'zustand';
import { accountApi, type Me, type ShareSummary } from './account';
import { api, followChat, regenerateChat, streamChat } from './api';
import { applyItem, finishTurn, userMessage, type ChatMessage } from './messages';
import type { ArtifactMeta, ChatSummary, StreamItem, Suggestion } from './types';
import { currentViewContext } from './view-context';

/**
 * Estado de la app: chats, mensajes y artifacts.
 *
 * Los mensajes se guardan POR CHAT: un turno en curso sigue recibiendo sus
 * eventos aunque el usuario esté mirando otro chat, y al volver se ve al día.
 * El turno corre en el servidor (no depende de esta conexión); "Detener" lo
 * cancela allá. assistant-ui no guarda nada: lee de acá (ExternalStoreRuntime).
 */
interface ThreadState {
  messages: ChatMessage[];
  running: boolean;
}

interface AppState {
  chats: ChatSummary[];
  chatsLoading: boolean;
  chatId: string | null; // null = chat nuevo todavía no creado en el servidor
  threads: Record<string, ThreadState>;
  threadLoading: boolean;
  artifacts: ArtifactMeta[];
  artifactId: string | null;
  artifactVersion: number; // sube cuando el agente toca el artifact abierto
  /** Panel Conocimiento abierto (ocupa el lugar del artifact). */
  knowledgeOpen: boolean;
  /** Tabla o concepto a mostrar al abrir el panel (null = la lista). */
  knowledgeTarget: string | null;
  knowledgeVersion: number; // sube cuando el agente guarda conocimiento
  /** La cuenta (null mientras carga). */
  me: Me | null;
  /** Espacio que se mira en la barra: null = personal, o el id de un proyecto. */
  projectId: string | null;
  /** Chats compartidos en el proyecto elegido. */
  shares: ShareSummary[];
  /** Copia compartida abierta en el panel derecho. */
  shareId: string | null;
  /**
   * Dashboard que el agente acaba de escribir en este chat: al terminar el turno se
   * "arma", y si la página reporta problemas se le pide que los corrija (lib/repair.ts).
   */
  repair: { artifactId: string; chatId: string; attempts: number; armed: boolean } | null;
  homeSuggestions: Suggestion[];
  followups: Record<string, Suggestion[]>;
  /** Reenganche a un turno en curso (componente connection-state). */
  connection: Record<string, 'reconnecting' | 'resumed' | undefined>;

  loadChats: () => Promise<void>;
  loadArtifacts: () => Promise<void>;
  newChat: () => void;
  openChat: (id: string) => Promise<void>;
  send: (text: string) => Promise<void>;
  cancel: () => Promise<void>;
  regenerate: () => Promise<void>;
  feedback: (type: 'positive' | 'negative', text: string) => void;
  loadSuggestions: (chatId?: string) => Promise<void>;
  renameChat: (id: string, title: string) => Promise<void>;
  setArchived: (id: string, archived: boolean) => Promise<void>;
  deleteChat: (id: string) => Promise<void>;
  openArtifact: (id: string) => void;
  closeArtifact: () => void;
  openKnowledge: (target?: string | null) => void;
  closeKnowledge: () => void;
  loadMe: () => Promise<void>;
  setProject: (id: string | null) => void;
  loadShares: () => Promise<void>;
  openShare: (id: string) => void;
  closeShare: () => void;
  runRepair: (message: string) => void;
  clearRepair: () => void;
}

const EMPTY: ThreadState = { messages: [], running: false };
// Herramientas que cambian un artifact abierto: el panel se recarga.
const WRITES = /^(artifact_|write_db$|upload_asset$|delete_asset$)/;
// Conexiones de lectura abiertas, por chat. Cerrarlas NO detiene el turno en el servidor.
const streams = new Map<string, AbortController>();

export const useAppStore = create<AppState>((set, get) => {
  const updateThread = (id: string, fn: (t: ThreadState) => ThreadState) =>
    set((s) => ({ threads: { ...s.threads, [id]: fn(s.threads[id] ?? EMPTY) } }));

  const setChatRunning = (id: string, running: boolean) =>
    set((s) => ({ chats: s.chats.map((c) => (c.id === id ? { ...c, running } : c)) }));

  /** Aplica un item al chat que corresponde, se esté mirando o no. */
  const onItem = (id: string) => (item: StreamItem) => {
    updateThread(id, (t) => ({ ...t, messages: applyItem(t.messages, item, true) }));
    const visible = get().chatId === id;
    // El agente tocó un dashboard en el chat que se mira: queda para revisar al terminar el turno.
    const touched = item.type === 'artifact' ? item.artifact_id : item.type === 'tool_result' && WRITES.test(item.name) ? get().artifactId : null;
    if (visible && touched) {
      const prev = get().repair;
      const same = prev && prev.artifactId === touched && prev.chatId === id;
      set({ repair: { artifactId: touched, chatId: id, attempts: same ? prev.attempts : 0, armed: false } });
    }
    if (item.type === 'artifact') {
      // Solo se abre solo en el chat que se está mirando; si no, se refresca si ya estaba abierto.
      if (visible) set((s) => ({ artifactId: item.artifact_id, artifactVersion: s.artifactVersion + 1, knowledgeOpen: false }));
      else if (get().artifactId === item.artifact_id) set((s) => ({ artifactVersion: s.artifactVersion + 1 }));
      get().loadArtifacts();
    } else if (item.type === 'tool_result' && WRITES.test(item.name) && get().artifactId) {
      set((s) => ({ artifactVersion: s.artifactVersion + 1 }));
    } else if (item.type === 'tool_result' && item.name === 'knowledge_propose') {
      set((s) => ({ knowledgeVersion: s.knowledgeVersion + 1 }));
    }
  };

  /** Lee un stream de turno hasta el final y deja el chat cerrado. */
  const consume = async (id: string, read: (signal: AbortSignal) => Promise<void>) => {
    streams.get(id)?.abort();
    const ctrl = new AbortController();
    streams.set(id, ctrl);
    updateThread(id, (t) => ({ ...t, running: true }));
    setChatRunning(id, true);
    try {
      await read(ctrl.signal);
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        onItem(id)({ type: 'error', message: `No se pudo completar: ${(e as Error).message}` });
      }
    } finally {
      if (streams.get(id) === ctrl) {
        streams.delete(id);
        updateThread(id, (t) => ({ messages: finishTurn(t.messages), running: false }));
        setChatRunning(id, false);
        const repair = get().repair;
        if (repair && repair.chatId === id && !repair.armed) set({ repair: { ...repair, armed: true } });
        get().loadChats();
        // Sugerencias de seguimiento para este chat y de inicio actualizadas.
        void get().loadSuggestions(id);
        void get().loadSuggestions();
      }
    }
  };

  return {
    chats: [],
    chatsLoading: true,
    chatId: null,
    threads: {},
    threadLoading: false,
    artifacts: [],
    artifactId: null,
    artifactVersion: 0,
    knowledgeOpen: false,
    knowledgeTarget: null,
    knowledgeVersion: 0,
    me: null,
    projectId: null,
    shares: [],
    shareId: null,
    repair: null,
    homeSuggestions: [],
    followups: {},
    connection: {},

    async loadChats() {
      const chats = await api.listChats().catch(() => get().chats);
      set({ chats, chatsLoading: false });
    },

    async loadArtifacts() {
      const project = get().projectId;
      const artifacts = await api.listArtifacts(project).catch(() => get().artifacts);
      if (get().projectId === project) set({ artifacts });
    },

    newChat() {
      set({ chatId: null, threadLoading: false });
    },

    async openChat(id) {
      set({ chatId: id });
      const cached = get().threads[id];
      // Con un turno en curso seguido desde acá, lo que hay en memoria está al día.
      if (cached?.running && streams.has(id)) return;
      set({ threadLoading: !cached });
      try {
        const chat = await api.getChat(id);
        const history = finishTurn(chat.items.reduce<ChatMessage[]>((ms, it) => applyItem(ms, it), []));
        updateThread(id, (t) => ({ ...t, messages: history }));
        // Turno en curso (de otra pestaña o de antes de recargar): engancharse.
        if (chat.running && !streams.has(id)) {
          const setConn = (v: 'reconnecting' | 'resumed' | undefined) =>
            set((s) => ({ connection: { ...s.connection, [id]: v } }));
          setConn('reconnecting');
          const handle = onItem(id);
          let first = true;
          void consume(id, (signal) =>
            followChat(
              id,
              (item) => {
                if (first) {
                  first = false;
                  setConn('resumed');
                  window.setTimeout(() => setConn(undefined), 2500);
                }
                handle(item);
              },
              signal,
            ),
          );
        } else if (!chat.running && !get().followups[id]) {
          void get().loadSuggestions(id);
        }
      } finally {
        if (get().chatId === id) set({ threadLoading: false });
      }
    },

    async send(text) {
      let id = get().chatId;
      if (!id) {
        const chat = await api.createChat();
        id = chat.id;
        set((s) => ({ chatId: id, chats: [{ ...chat, title: text.slice(0, 70), running: true }, ...s.chats] }));
      }
      const chatId = id;
      updateThread(chatId, (t) => ({ ...t, messages: [...t.messages, userMessage(text)] }));
      set((s) => ({ followups: { ...s.followups, [chatId]: [] } }));
      // El servidor repite el turno desde el mensaje del usuario: ese ya está en pantalla.
      let skippedEcho = false;
      const handle = onItem(chatId);
      await consume(chatId, (signal) =>
        streamChat(
          chatId,
          text,
          // Con un dashboard abierto al lado, el mensaje lleva dónde está la persona (sin valores).
          get().artifactId ? currentViewContext() : null,
          (item) => {
            if (!skippedEcho && item.type === 'text' && item.role === 'user') {
              skippedEcho = true;
              return;
            }
            handle(item);
          },
          signal,
        ),
      );
    },

    async cancel() {
      const id = get().chatId;
      if (!id) return;
      const ctrl = streams.get(id);
      await api.cancelChat(id).catch(() => undefined);
      // El stream recibe "cancelled" y se cierra solo; por las dudas, se corta este
      // mismo stream (no uno nuevo si el usuario ya mandó otro mensaje).
      window.setTimeout(() => {
        if (ctrl && streams.get(id) === ctrl) ctrl.abort();
      }, 3000);
    },

    async regenerate() {
      const id = get().chatId;
      if (!id || streams.has(id)) return;
      // Se descarta la última respuesta: queda hasta el último mensaje del usuario.
      updateThread(id, (t) => {
        const lastUser = t.messages.map((m) => m.role).lastIndexOf('user');
        return { ...t, messages: lastUser >= 0 ? t.messages.slice(0, lastUser + 1) : t.messages };
      });
      set((s) => ({ followups: { ...s.followups, [id]: [] } }));
      let skippedEcho = false;
      const handle = onItem(id);
      await consume(id, (signal) =>
        regenerateChat(
          id,
          (item) => {
            if (!skippedEcho && item.type === 'text' && item.role === 'user') {
              skippedEcho = true;
              return;
            }
            handle(item);
          },
          signal,
        ),
      );
    },

    feedback(type, text) {
      const id = get().chatId;
      if (id) void api.feedback(id, { type, text }).catch(() => undefined);
    },

    async loadSuggestions(chatId) {
      const list = await api.suggestions(chatId).catch(() => null);
      if (!list) return;
      if (chatId) set((s) => ({ followups: { ...s.followups, [chatId]: list } }));
      else set({ homeSuggestions: list });
    },

    async renameChat(id, title) {
      set((s) => ({ chats: s.chats.map((c) => (c.id === id ? { ...c, title } : c)) }));
      await api.updateChat(id, { title });
    },

    async setArchived(id, archived) {
      set((s) => ({ chats: s.chats.map((c) => (c.id === id ? { ...c, archived } : c)) }));
      await api.updateChat(id, { archived });
      if (archived && get().chatId === id) get().newChat();
    },

    async deleteChat(id) {
      streams.get(id)?.abort();
      set((s) => {
        const threads = { ...s.threads };
        delete threads[id];
        return { chats: s.chats.filter((c) => c.id !== id), threads };
      });
      await api.deleteChat(id);
      if (get().chatId === id) get().newChat();
    },

    openArtifact(id) {
      set((s) => ({ artifactId: id, artifactVersion: s.artifactVersion + 1, knowledgeOpen: false, shareId: null }));
    },

    closeArtifact() {
      set({ artifactId: null });
    },

    openKnowledge(target = null) {
      set((s) => ({ knowledgeOpen: true, knowledgeTarget: target, knowledgeVersion: s.knowledgeVersion + 1, shareId: null }));
    },

    closeKnowledge() {
      set({ knowledgeOpen: false, knowledgeTarget: null });
    },

    async loadMe() {
      const me = await accountApi.me().catch(() => null);
      if (!me) return;
      // Si el proyecto elegido ya no está (te sacaron o se archivó), se vuelve a personal.
      const keep = me.projects.some((p) => p.id === get().projectId);
      set({ me, ...(keep ? {} : { projectId: null, shares: [] }) });
    },

    setProject(id) {
      try {
        if (id) localStorage.setItem('project', id);
        else localStorage.removeItem('project');
      } catch {
        /* sin storage */
      }
      set({ projectId: id, artifacts: [], shares: [] });
      void get().loadArtifacts();
      void get().loadShares();
    },

    async loadShares() {
      const id = get().projectId;
      if (!id) return set({ shares: [] });
      const detail = await accountApi.project(id).catch(() => null);
      if (get().projectId === id) set({ shares: detail?.shares ?? [] });
    },

    runRepair(message) {
      const repair = get().repair;
      if (!repair || get().chatId !== repair.chatId) return;
      set({ repair: { ...repair, attempts: repair.attempts + 1, armed: false } });
      void get().send(message);
    },

    clearRepair() {
      set({ repair: null });
    },

    openShare(id) {
      set({ shareId: id, knowledgeOpen: false });
    },

    closeShare() {
      set({ shareId: null });
    },
  };
});

/** Mensajes y estado del chat que se está mirando. */
export const selectCurrent = (s: AppState): ThreadState => (s.chatId ? (s.threads[s.chatId] ?? EMPTY) : EMPTY);
