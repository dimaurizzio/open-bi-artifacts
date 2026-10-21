/** Saludo de la pantalla de inicio según la hora local y la hora que viaja con cada mensaje. */

type DayPart = 'mañana' | 'tarde' | 'noche' | 'madrugada';

export function dayPart(hour: number): DayPart {
  if (hour >= 5 && hour < 12) return 'mañana';
  if (hour >= 12 && hour < 20) return 'tarde';
  if (hour >= 20) return 'noche';
  return 'madrugada';
}

/** 'Lucía Fernanda Pérez' → 'Lucía'; un mail → lo de antes de la @. */
export function firstName(name?: string | null): string {
  let text = (name ?? '').trim();
  if (text.includes('@')) text = text.split('@')[0].replace(/[._]/g, ' ');
  const word = text.split(/\s+/)[0] ?? '';
  return word ? word[0].toUpperCase() + word.slice(1) : '';
}

const OPENERS: Record<DayPart, string> = {
  mañana: 'Buen día',
  tarde: 'Buenas tardes',
  noche: 'Buenas noches',
  madrugada: 'Hola',
};

const PROMPTS: Record<DayPart, string[]> = {
  mañana: ['¿Qué querés descubrir hoy?', '¿Con qué datos arrancamos el día?'],
  tarde: ['¿En qué datos nos metemos esta tarde?', '¿Qué querés saber de los datos?'],
  noche: ['¿Qué querés revisar antes de cerrar el día?', '¿Qué dato te quedó picando?'],
  madrugada: ['Trabajando a deshoras: ¿en qué te ayudo?', '¿Qué dato no te deja dormir?'],
};

/** Título y bajada de la bienvenida. La bajada varía por día, no en cada render. */
export function greeting(name?: string | null, now = new Date()): { title: string; prompt: string } {
  const part = dayPart(now.getHours());
  const who = firstName(name);
  const options = PROMPTS[part];
  return {
    title: who ? `${OPENERS[part]}, ${who}` : OPENERS[part],
    prompt: options[now.getDate() % options.length],
  };
}

/** Zona y desfasaje con UTC del navegador: el agente sabe qué hora es para la persona. */
export function clientTime(): { tz: string | null; utc_offset: number } {
  let tz: string | null = null;
  try {
    tz = Intl.DateTimeFormat().resolvedOptions().timeZone ?? null;
  } catch {
    /* navegador sin Intl */
  }
  return { tz, utc_offset: -new Date().getTimezoneOffset() };
}
