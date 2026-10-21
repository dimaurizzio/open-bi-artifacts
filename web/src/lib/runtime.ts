import { useExternalStoreRuntime, type AppendMessage } from '@assistant-ui/react';
import { useShallow } from 'zustand/react/shallow';
import { convertMessage } from './messages';
import type { Suggestion } from './types';

const EMPTY: Suggestion[] = [];
import { selectCurrent, useAppStore } from './store';



function textOf(message: AppendMessage): string {
  return message.content
    .map((p) => (p.type === 'text' ? p.text : ''))
    .join('\n')
    .trim();
}

/** Conecta assistant-ui con nuestro store (ExternalStoreRuntime): el estado es nuestro. */
export function useAppRuntime() {
  const s = useAppStore(
    useShallow((st) => ({
      messages: selectCurrent(st).messages,
      isRunning: selectCurrent(st).running,
      threadLoading: st.threadLoading,
      chats: st.chats,
      chatsLoading: st.chatsLoading,
      chatId: st.chatId,
      suggestions: st.chatId && selectCurrent(st).messages.length ? (st.followups[st.chatId] ?? EMPTY) : st.homeSuggestions,
    })),
  );

  return useExternalStoreRuntime({
    messages: s.messages,
    convertMessage,
    isRunning: s.isRunning,
    isLoading: s.threadLoading,
    suggestions: s.suggestions,
    onNew: async (message) => {
      const text = textOf(message);
      if (text) await useAppStore.getState().send(text);
    },
    onCancel: async () => useAppStore.getState().cancel(),
    // Regenerar: el servidor rebobina el último turno y lo vuelve a correr.
    onReload: async () => useAppStore.getState().regenerate(),
    adapters: {
      feedback: {
        submit: ({ message, type }) =>
          useAppStore.getState().feedback(
            type,
            message.content.map((p) => (p.type === 'text' ? p.text : '')).join('\n').slice(0, 2000),
          ),
      },
      threadList: {
        threadId: s.chatId ?? undefined,
        isLoading: s.chatsLoading,
        threads: s.chats
          .filter((c) => !c.archived)
          .map((c) => ({
            status: 'regular' as const,
            id: c.id,
            title: c.title,
            custom: { running: !!c.running, updatedAt: c.updated },
          })),
        archivedThreads: s.chats
          .filter((c) => c.archived)
          .map((c) => ({ status: 'archived' as const, id: c.id, title: c.title })),
        onSwitchToNewThread: () => useAppStore.getState().newChat(),
        onSwitchToThread: (id) => useAppStore.getState().openChat(id),
        onRename: (id, title) => useAppStore.getState().renameChat(id, title),
        onArchive: (id) => useAppStore.getState().setArchived(id, true),
        onUnarchive: (id) => useAppStore.getState().setArchived(id, false),
        onDelete: (id) => useAppStore.getState().deleteChat(id),
      },
    },
  });
}
