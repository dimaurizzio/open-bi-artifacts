import { useEffect, useState } from 'react';

export type ThemePref = 'system' | 'light' | 'dark';

function read(): ThemePref {
  try {
    const v = localStorage.getItem('theme');
    return v === 'light' || v === 'dark' ? v : 'system';
  } catch {
    return 'system';
  }
}

/** Preferencia de tema y tema efectivo. shadcn usa la clase `dark` en <html>. */
export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(read);
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches);

  useEffect(() => {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const on = () => setSystemDark(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);

  const effective: 'light' | 'dark' = pref === 'system' ? (systemDark ? 'dark' : 'light') : pref;

  useEffect(() => {
    document.documentElement.classList.toggle('dark', effective === 'dark');
    document.documentElement.style.colorScheme = effective;
    try {
      localStorage.setItem('theme', pref);
    } catch {
      /* sin storage */
    }
  }, [pref, effective]);

  return { pref, effective, setPref };
}
