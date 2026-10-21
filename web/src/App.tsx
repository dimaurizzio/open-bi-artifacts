import { AssistantRuntimeProvider } from '@assistant-ui/react';
import { MenuIcon, PanelRightOpenIcon } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { useDefaultLayout, usePanelRef, type LayoutStorage } from 'react-resizable-panels';
import { BigQueryConsentBanner, ShareChatButton, SharePanel } from '@/components/app/account';
import { AppSidebar, SidebarRail } from '@/components/app/app-sidebar';
import { ArtifactPanel } from '@/components/app/artifact-panel';
import { KnowledgePanel } from '@/components/app/knowledge-panel';
import { ToolUIs } from '@/components/app/tool-uis';
import { Thread } from '@/components/assistant-ui/thread.aui';
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button';
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from '@/components/ui/resizable';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { TooltipProvider } from '@/components/ui/tooltip';
import { useMediaQuery } from '@/hooks/use-media-query';
import { useAppRuntime } from '@/lib/runtime';
import { useAppStore } from '@/lib/store';
import { useModels } from '@/lib/models';
import { useTheme } from '@/lib/theme';

// /a/<id> muestra solo el artifact (enlace para compartir o pantalla completa).
const SOLO = location.pathname.match(/^\/a\/([\w-]+)/)?.[1] ?? null;

// Guarda el tamaño de los paneles entre sesiones (si el navegador lo permite).
const layoutStorage: LayoutStorage = {
  getItem: (k) => {
    try {
      return localStorage.getItem(k);
    } catch {
      return null;
    }
  },
  setItem: (k, v) => {
    try {
      localStorage.setItem(k, v);
    } catch {
      /* sin storage */
    }
  },
};

const RAIL = 52; // ancho de la barra lateral plegada (px)

export function App() {
  const theme = useTheme();
  return (
    <TooltipProvider delayDuration={300}>
      {SOLO ? (
        <div className="h-dvh">
          <ArtifactPanel artifactId={SOLO} version={0} theme={theme.effective} solo onClose={() => undefined} />
        </div>
      ) : (
        <ChatApp theme={theme} />
      )}
    </TooltipProvider>
  );
}

function ChatApp({ theme }: { theme: ReturnType<typeof useTheme> }) {
  const runtime = useAppRuntime();
  const artifactId = useAppStore((s) => s.artifactId);
  const artifactVersion = useAppStore((s) => s.artifactVersion);
  const knowledgeOpen = useAppStore((s) => s.knowledgeOpen);
  const shareId = useAppStore((s) => s.shareId);
  const chats = useAppStore((s) => s.chats);
  const chatId = useAppStore((s) => s.chatId);
  const lastArtifact = useAppStore((s) => s.artifacts[0]?.id);
  const isDesktop = useMediaQuery('(min-width: 768px)');
  const [sheetOpen, setSheetOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const sidebarRef = usePanelRef();

  const sideOpen = !!artifactId || knowledgeOpen || !!shareId;
  const panelIds = useMemo(() => (sideOpen ? ['sidebar', 'chat', 'artifact'] : ['sidebar', 'chat']), [sideOpen]);
  const layout = useDefaultLayout({ id: 'app-layout', panelIds, storage: layoutStorage });

  useEffect(() => {
    const store = useAppStore.getState();
    // Entrar a la app arranca en un chat nuevo, con el saludo. Solo se abre un chat
    // si la URL lo nombra (recargar la página o un enlace a ese chat).
    // Se lee ya: el efecto que sincroniza la URL corre antes de que lleguen los chats y la limpia.
    const fromHash = new URLSearchParams(location.hash.slice(1)).get('chat');
    store.loadChats().then(() => {
      if (fromHash) store.openChat(fromHash);
    });
    // La cuenta primero: el espacio guardado (un proyecto) solo vale si seguís siendo miembro.
    store.loadMe().then(() => {
      let saved: string | null = null;
      try {
        saved = localStorage.getItem('project');
      } catch {
        /* sin storage */
      }
      const me = useAppStore.getState().me;
      if (saved && me?.projects.some((p) => p.id === saved)) store.setProject(saved);
      else store.loadArtifacts();
    });
    store.loadSuggestions();
    void useModels.getState().load();
    const art = new URLSearchParams(location.hash.slice(1)).get('artifact');
    if (art) store.openArtifact(art);
  }, []);

  // Estado navegable en la URL: chat y artifact abiertos.
  useEffect(() => {
    const params = new URLSearchParams();
    if (chatId) params.set('chat', chatId);
    if (artifactId) params.set('artifact', artifactId);
    const hash = params.toString();
    history.replaceState(null, '', hash ? `#${hash}` : location.pathname);
  }, [chatId, artifactId]);

  const toggleSidebar = () => {
    if (!isDesktop) return setSheetOpen((o) => !o);
    const panel = sidebarRef.current;
    if (!panel) return;
    if (panel.isCollapsed()) panel.expand();
    else panel.collapse();
  };

  // Ctrl/Cmd + B pliega o despliega la barra lateral.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        toggleSidebar();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const title = chats.find((c) => c.id === chatId)?.title ?? 'Chat nuevo';
  const closeArtifact = () => useAppStore.getState().closeArtifact();

  const chat = (
    <main className="flex h-full min-w-0 flex-col">
      <header className="flex h-12 shrink-0 items-center gap-1 border-b px-2">
        {!isDesktop && (
          <TooltipIconButton tooltip="Menú" onClick={toggleSidebar}>
            <MenuIcon />
          </TooltipIconButton>
        )}
        <h1 className="min-w-0 flex-1 truncate px-2 text-sm font-medium">{title}</h1>
        <ShareChatButton chatId={chatId} />
        {!sideOpen && lastArtifact && (
          <TooltipIconButton tooltip="Abrir el último artifact" onClick={() => useAppStore.getState().openArtifact(lastArtifact)}>
            <PanelRightOpenIcon />
          </TooltipIconButton>
        )}
      </header>
      <BigQueryConsentBanner />
      <div className="min-h-0 flex-1">
        <Thread />
      </div>
    </main>
  );

  // El panel derecho muestra un chat compartido, el Conocimiento o el artifact abierto.
  const artifact = shareId ? (
    <SharePanel shareId={shareId} onClose={() => useAppStore.getState().closeShare()} />
  ) : knowledgeOpen ? (
    <KnowledgePanel onClose={() => useAppStore.getState().closeKnowledge()} />
  ) : artifactId ? (
    <ArtifactPanel artifactId={artifactId} version={artifactVersion} theme={theme.effective} solo={false} onClose={closeArtifact} />
  ) : null;

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <ToolUIs />
      {isDesktop ? (
        <ResizablePanelGroup
          orientation="horizontal"
          className="bg-background text-foreground"
          defaultLayout={layout.defaultLayout}
          onLayoutChanged={layout.onLayoutChanged}
        >
          <ResizablePanel
            id="sidebar"
            panelRef={sidebarRef}
            collapsible
            collapsedSize={RAIL}
            minSize={200}
            maxSize={420}
            defaultSize={264}
            onResize={(size) => setCollapsed(size.inPixels < RAIL + 20)}
          >
            {collapsed ? (
              <SidebarRail onExpand={() => sidebarRef.current?.expand()} theme={theme.pref} onTheme={theme.setPref} />
            ) : (
              <AppSidebar theme={theme.pref} onTheme={theme.setPref} onCollapse={() => sidebarRef.current?.collapse()} />
            )}
          </ResizablePanel>
          <ResizableHandle />
          <ResizablePanel id="chat" minSize={360}>
            {chat}
          </ResizablePanel>
          {artifact && (
            <>
              <ResizableHandle withHandle />
              <ResizablePanel id="artifact" minSize={380} defaultSize="55">
                {artifact}
              </ResizablePanel>
            </>
          )}
        </ResizablePanelGroup>
      ) : (
        <div className="h-dvh bg-background text-foreground">
          <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
            <SheetContent side="left" className="w-72 p-0">
              <SheetTitle className="sr-only">Menú</SheetTitle>
              <AppSidebar theme={theme.pref} onTheme={theme.setPref} onNavigate={() => setSheetOpen(false)} />
            </SheetContent>
          </Sheet>
          {artifact ? <div className="h-full">{artifact}</div> : chat}
        </div>
      )}
    </AssistantRuntimeProvider>
  );
}
