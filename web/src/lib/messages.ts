import type { ThreadMessageLike } from '@assistant-ui/react';
import type { StreamItem } from './types';

/**
 * Mensajes del chat en nuestro formato. Un turno del agente (texto, llamadas a
 * herramientas con su resultado, más texto) es UN mensaje de assistant con sus
 * partes en orden, que es como assistant-ui agrupa y dibuja las herramientas.
 */
export type ChatPart =
  | { type: 'text'; text: string; streaming?: boolean }
  | {
      type: 'tool-call';
      toolCallId: string;
      toolName: string;
      args: Record<string, unknown>;
      result?: unknown;
      isError?: boolean;
    }
  | { type: 'data-artifact'; id: string; data: { artifactId: string } };

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  parts: ChatPart[];
  createdAt: Date;
  running?: boolean;
  cancelled?: boolean;
  error?: string;
  models?: string[];
}

let seq = 0;
const uid = (prefix: string) => `${prefix}-${Date.now().toString(36)}-${++seq}`;

export function userMessage(text: string): ChatMessage {
  return { id: uid('u'), role: 'user', parts: [{ type: 'text', text }], createdAt: new Date() };
}

function closeText(parts: ChatPart[]): ChatPart[] {
  const last = parts[parts.length - 1];
  return last?.type === 'text' && last.streaming ? [...parts.slice(0, -1), { ...last, streaming: false }] : parts;
}

/** Devuelve una copia con el turno actual del agente (lo crea si hace falta). */
function withAssistant(messages: ChatMessage[], running: boolean, fn: (m: ChatMessage) => ChatMessage): ChatMessage[] {
  const last = messages[messages.length - 1];
  if (last?.role === 'assistant') return [...messages.slice(0, -1), fn(last)];
  const fresh: ChatMessage = { id: uid('a'), role: 'assistant', parts: [], createdAt: new Date(), running };
  return [...messages, fn(fresh)];
}

function addModel(m: ChatMessage, model?: string | null): ChatMessage {
  if (!model || m.models?.includes(model)) return m;
  return { ...m, models: [...(m.models ?? []), model] };
}

/** Agrega un item del stream (función pura): sirve para historial y para streaming. */
export function applyItem(messages: ChatMessage[], item: StreamItem, running = false): ChatMessage[] {
  switch (item.type) {
    case 'text': {
      if (item.role === 'user') return [...messages, userMessage(item.text)];
      return withAssistant(messages, running, (m) => {
        const parts = m.parts.slice();
        const last = parts[parts.length - 1];
        if (last?.type === 'text' && last.streaming) {
          parts[parts.length - 1] = item.partial
            ? { ...last, text: last.text + item.text }
            : { type: 'text', text: item.text, streaming: false };
        } else {
          parts.push({ type: 'text', text: item.text, streaming: item.partial });
        }
        return addModel({ ...m, parts }, item.model);
      });
    }
    case 'tool_call':
      return withAssistant(messages, running, (m) =>
        addModel(
          {
            ...m,
            parts: [
              ...closeText(m.parts),
              { type: 'tool-call', toolCallId: item.id || uid('t'), toolName: item.name, args: item.args ?? {} },
            ],
          },
          item.model,
        ),
      );
    case 'tool_result':
      return withAssistant(messages, running, (m) => {
        const idx = m.parts.findIndex(
          (p) =>
            p.type === 'tool-call' &&
            (item.id ? p.toolCallId === item.id : p.toolName === item.name && p.result === undefined),
        );
        if (idx < 0) return m;
        const parts = m.parts.slice();
        parts[idx] = { ...(parts[idx] as Extract<ChatPart, { type: 'tool-call' }>), result: item.result ?? null, isError: !item.ok };
        return { ...m, parts };
      });
    case 'artifact':
      return withAssistant(messages, running, (m) =>
        m.parts.some((p) => p.type === 'data-artifact' && p.data.artifactId === item.artifact_id)
          ? m
          : {
              ...m,
              parts: [
                ...closeText(m.parts),
                { type: 'data-artifact', id: `art-${item.artifact_id}`, data: { artifactId: item.artifact_id } },
              ],
            },
      );
    case 'error':
      return withAssistant(messages, running, (m) => ({ ...m, parts: closeText(m.parts), error: item.message }));
    case 'cancelled':
      return finishTurn(messages, true);
    default:
      return messages;
  }
}

/** Cierra el turno en curso (fin del stream o cancelación). */
export function finishTurn(messages: ChatMessage[], cancelled = false): ChatMessage[] {
  return messages.map((m) =>
    m.role === 'assistant' && m.running ? { ...m, running: false, cancelled, parts: closeText(m.parts) } : m,
  );
}

export function convertMessage(m: ChatMessage): ThreadMessageLike {
  const content = m.parts.map((p) => {
    if (p.type === 'tool-call') {
      return {
        type: 'tool-call' as const,
        toolCallId: p.toolCallId,
        toolName: p.toolName,
        args: p.args as never,
        argsText: JSON.stringify(p.args, null, 2),
        ...(p.result !== undefined && { result: p.result, isError: p.isError }),
      };
    }
    if (p.type === 'data-artifact') return { type: 'data-artifact' as const, id: p.id, data: p.data };
    return { type: 'text' as const, text: p.text };
  });
  return {
    id: m.id,
    role: m.role,
    createdAt: m.createdAt,
    content,
    status:
      m.role !== 'assistant'
        ? undefined
        : m.error
          ? { type: 'incomplete', reason: 'error', error: m.error }
          : m.cancelled
            ? { type: 'incomplete', reason: 'cancelled' }
            : m.running
              ? { type: 'running' }
              : { type: 'complete', reason: 'stop' },
    metadata: { custom: { models: m.models ?? [] } },
  };
}
