import {
  BookOpenIcon,
  ChartColumnIcon,
  FileTextIcon,
  LaptopIcon,
  MessagesSquareIcon,
  MoonIcon,
  PanelLeftCloseIcon,
  PanelLeftOpenIcon,
  SquarePenIcon,
  SunIcon,
} from 'lucide-react';
import { SharedChats, SpaceSwitcher, UserMenu } from '@/components/app/account';
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button';
import { ThreadList } from '@/components/assistant-ui/thread-list.aui';
import { useAppStore } from '@/lib/store';
import type { ThemePref } from '@/lib/theme';
import { cn } from '@/lib/utils';

const THEMES: { value: ThemePref; label: string; Icon: typeof SunIcon }[] = [
  { value: 'system', label: 'Sistema', Icon: LaptopIcon },
  { value: 'light', label: 'Claro', Icon: SunIcon },
  { value: 'dark', label: 'Oscuro', Icon: MoonIcon },
];

export function AppSidebar({
  theme,
  onTheme,
  onNavigate,
  onCollapse,
}: {
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
  onNavigate?: () => void;
  /** En escritorio: pliega la barra al riel. En celular no hay (el panel se cierra solo). */
  onCollapse?: () => void;
}) {
  const artifacts = useAppStore((s) => s.artifacts);
  const artifactId = useAppStore((s) => s.artifactId);
  const openArtifact = useAppStore((s) => s.openArtifact);
  const knowledgeOpen = useAppStore((s) => s.knowledgeOpen);
  const openKnowledge = useAppStore((s) => s.openKnowledge);
  const closeKnowledge = useAppStore((s) => s.closeKnowledge);
  const inProject = useAppStore((s) => !!s.projectId);

  return (
    <div className="flex h-full flex-col bg-sidebar text-sidebar-foreground">
      {/* Misma altura y posición que en el riel: el botón no se mueve al plegar. */}
      <div className="flex h-12 shrink-0 items-center gap-1 px-2.5">
        {onCollapse && (
          <TooltipIconButton tooltip="Ocultar barra lateral (Ctrl+B)" side="right" onClick={onCollapse} className="size-8 shrink-0">
            <PanelLeftCloseIcon />
          </TooltipIconButton>
        )}
        <div className="min-w-0 flex-1">
          <SpaceSwitcher />
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto">
        <div className="flex flex-col gap-6 px-3 pb-4" onClick={(e) => (e.target as HTMLElement).closest('a,button') && onNavigate?.()}>
          <button
            type="button"
            onClick={() => (knowledgeOpen ? closeKnowledge() : openKnowledge())}
            className={cn(
              'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start text-sm transition-colors hover:bg-sidebar-accent',
              knowledgeOpen && 'bg-sidebar-accent font-medium',
            )}
          >
            <BookOpenIcon className="size-4 shrink-0 text-muted-foreground" />
            Conocimiento
          </button>

          <section>
            <h2 className="px-2 pb-1.5 text-xs font-medium text-muted-foreground">Tus chats</h2>
            <ThreadList />
          </section>

          <section>
            <h2 className="px-2 pb-1.5 text-xs font-medium text-muted-foreground">{inProject ? 'Dashboards del proyecto' : 'Tus artifacts'}</h2>
            {artifacts.length === 0 && (
              <p className="px-2 text-xs text-muted-foreground">
                {inProject ? 'Todavía no hay dashboards. Movés uno desde su menú (Mover a…).' : 'Todavía no hay artifacts.'}
              </p>
            )}
            <ul className="flex flex-col gap-0.5">
              {artifacts.map((a) => {
                const Icon = a.kind === 'analysis' ? FileTextIcon : ChartColumnIcon;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => openArtifact(a.id)}
                      className={cn(
                        'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start text-sm transition-colors hover:bg-sidebar-accent',
                        a.id === artifactId && 'bg-sidebar-accent font-medium',
                      )}
                    >
                      <Icon className="size-4 shrink-0 text-muted-foreground" />
                      <span className="truncate">{a.title}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>

          <SharedChats />
        </div>
      </div>

      <div className="border-t p-2">
        <UserMenu theme={theme} onTheme={onTheme} />
      </div>
    </div>
  );
}

/** Barra lateral plegada: solo íconos. */
export function SidebarRail({
  onExpand,
  theme,
  onTheme,
}: {
  onExpand: () => void;
  theme: ThemePref;
  onTheme: (t: ThemePref) => void;
}) {
  const newChat = useAppStore((s) => s.newChat);
  const current = THEMES.find((t) => t.value === theme) ?? THEMES[0];
  const next = THEMES[(THEMES.indexOf(current) + 1) % THEMES.length];
  return (
    <div className="flex h-full flex-col items-center gap-1 bg-sidebar pb-3 text-sidebar-foreground">
      <div className="flex h-12 shrink-0 items-center">
        <TooltipIconButton tooltip="Mostrar barra lateral (Ctrl+B)" side="right" onClick={onExpand} className="size-8">
          <PanelLeftOpenIcon />
        </TooltipIconButton>
      </div>
      <TooltipIconButton tooltip="Nuevo chat" side="right" onClick={newChat} className="size-8">
        <SquarePenIcon />
      </TooltipIconButton>
      <TooltipIconButton tooltip="Chats y artifacts" side="right" onClick={onExpand} className="size-8">
        <MessagesSquareIcon />
      </TooltipIconButton>
      <TooltipIconButton tooltip="Conocimiento" side="right" onClick={() => useAppStore.getState().openKnowledge()} className="size-8">
        <BookOpenIcon />
      </TooltipIconButton>
      <span className="flex-1" />
      <TooltipIconButton tooltip={`Tema: ${current.label.toLowerCase()} (cambiar a ${next.label.toLowerCase()})`} side="right" onClick={() => onTheme(next.value)} className="size-8">
        <current.Icon />
      </TooltipIconButton>
    </div>
  );
}
