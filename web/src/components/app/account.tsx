import {
  ArrowRightLeftIcon,
  CheckIcon,
  ChevronsUpDownIcon,
  FolderIcon,
  LoaderIcon,
  LogOutIcon,
  MessageSquareShareIcon,
  PlusIcon,
  SettingsIcon,
  ShieldIcon,
  TriangleAlertIcon,
  UserIcon,
  UsersIcon,
  XIcon,
} from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { TooltipIconButton } from '@/components/assistant-ui/tooltip-icon-button';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Skeleton } from '@/components/ui/skeleton';
import { ROLE_HELP, ROLE_LABELS, accountApi, type AdminUser, type Member, type ProjectDetail, type ProjectRole, type Share } from '@/lib/account';
import { ago, toolLabel } from '@/lib/format';
import { useAppStore } from '@/lib/store';
import type { ThemePref } from '@/lib/theme';
import { cn } from '@/lib/utils';

const selectClass =
  'h-8 rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30';

function Avatar({ name, picture, className }: { name: string; picture?: string | null; className?: string }) {
  const [broken, setBroken] = useState(false);
  if (picture && !broken) {
    return <img src={picture} alt="" referrerPolicy="no-referrer" onError={() => setBroken(true)} className={cn('size-6 shrink-0 rounded-full', className)} />;
  }
  return (
    <span className={cn('flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-[11px] font-medium uppercase', className)}>
      {name.slice(0, 1)}
    </span>
  );
}

// --- espacio: personal o un proyecto -----------------------------------------------------------

/** Selector de espacio en la barra lateral: lo personal o un proyecto. */
export function SpaceSwitcher() {
  const me = useAppStore((s) => s.me);
  const projectId = useAppStore((s) => s.projectId);
  const setProject = useAppStore((s) => s.setProject);
  const [dialog, setDialog] = useState<'new' | 'manage' | null>(null);
  const current = me?.projects.find((p) => p.id === projectId);

  return (
    <div className="flex items-center gap-1">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" className="h-8 min-w-0 flex-1 justify-start gap-2 px-2">
            {current ? <FolderIcon className="size-4 shrink-0 text-muted-foreground" /> : <UserIcon className="size-4 shrink-0 text-muted-foreground" />}
            <span className="truncate font-medium">{current?.name ?? 'Personal'}</span>
            <ChevronsUpDownIcon className="ms-auto size-3.5 shrink-0 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-60">
          <DropdownMenuItem onSelect={() => setProject(null)}>
            <UserIcon /> Personal {!projectId && <CheckIcon className="ms-auto" />}
          </DropdownMenuItem>
          {me && me.projects.length > 0 && <DropdownMenuSeparator />}
          {me?.projects.map((p) => (
            <DropdownMenuItem key={p.id} onSelect={() => setProject(p.id)}>
              <FolderIcon />
              <span className="truncate">{p.name}</span>
              {p.id === projectId ? <CheckIcon className="ms-auto" /> : <span className="ms-auto text-xs text-muted-foreground">{ROLE_LABELS[p.role]}</span>}
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setDialog('new')}>
            <PlusIcon /> Nuevo proyecto
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {current && (
        <TooltipIconButton tooltip="Miembros y ajustes del proyecto" onClick={() => setDialog('manage')} className="size-8">
          <SettingsIcon />
        </TooltipIconButton>
      )}
      <NewProjectDialog open={dialog === 'new'} onClose={() => setDialog(null)} />
      {current && <ProjectDialog projectId={current.id} open={dialog === 'manage'} onClose={() => setDialog(null)} />}
    </div>
  );
}

function NewProjectDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setName('');
      setError(null);
    }
  }, [open]);
  const create = async () => {
    setBusy(true);
    try {
      const p = await accountApi.createProject(name.trim());
      await useAppStore.getState().loadMe();
      useAppStore.getState().setProject(p.id);
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>Nuevo proyecto</DialogTitle>
          <DialogDescription>Un lugar compartido para dashboards y chats. Después sumás a quien quieras.</DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault();
            void create();
          }}
        >
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Nombre, p. ej. Audiencias" autoFocus required maxLength={80} />
          {error && <p className="text-sm text-destructive">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancelar
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy && <LoaderIcon className="size-4 animate-spin" />} Crear
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Miembros, nombre y archivado de un proyecto. Solo los owners cambian algo. */
function ProjectDialog({ projectId, open, onClose }: { projectId: string; open: boolean; onClose: () => void }) {
  const me = useAppStore((s) => s.me);
  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [role, setRole] = useState<ProjectRole>('viewer');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirmArchive, setConfirmArchive] = useState(false);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setConfirmArchive(false);
    accountApi
      .project(projectId)
      .then((d) => {
        setDetail(d);
        setName(d.name);
      })
      .catch((e: Error) => setError(e.message));
  }, [open, projectId]);

  const owner = detail?.role === 'owner';
  const act = async (fn: () => Promise<{ members?: Member[] } | unknown>) => {
    setBusy(true);
    setError(null);
    try {
      const r = (await fn()) as { members?: Member[] };
      if (r && r.members) setDetail((d) => (d ? { ...d, members: r.members! } : d));
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const leave = async () => {
    if (!me) return;
    if (await act(() => accountApi.removeMember(projectId, me.id))) {
      useAppStore.getState().setProject(null);
      await useAppStore.getState().loadMe();
      onClose();
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{detail?.name ?? 'Proyecto'}</DialogTitle>
          <DialogDescription>
            Los dashboards del proyecto los ven todos sus miembros. Cada uno consulta BigQuery con su propia cuenta, así que
            alguien sin permiso sobre una tabla no ve esos datos.
          </DialogDescription>
        </DialogHeader>
        {!detail ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <div className="flex flex-col gap-4">
            {owner && (
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void act(async () => {
                    await accountApi.renameProject(projectId, name.trim());
                    await useAppStore.getState().loadMe();
                  });
                }}
              >
                <Input value={name} onChange={(e) => setName(e.target.value)} aria-label="Nombre del proyecto" maxLength={80} />
                <Button type="submit" variant="outline" disabled={busy || !name.trim() || name.trim() === detail.name}>
                  Renombrar
                </Button>
              </form>
            )}

            <section className="flex flex-col gap-2">
              <h3 className="text-sm font-medium">Miembros</h3>
              <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto">
                {detail.members.map((m) => (
                  <li key={m.user_id} className="flex items-center gap-2 rounded-md px-1 py-1">
                    <Avatar name={m.name || m.user_id} picture={m.picture} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm">{m.name || m.user_id}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {m.name ? m.user_id : ''}
                        {!m.last_login_at && ' · todavía no entró'}
                      </span>
                    </span>
                    {owner ? (
                      <>
                        <select
                          className={selectClass}
                          value={m.role}
                          onChange={(e) => void act(() => accountApi.setRole(projectId, m.user_id, e.target.value as ProjectRole))}
                          aria-label={`Rol de ${m.user_id}`}
                        >
                          {(Object.keys(ROLE_LABELS) as ProjectRole[]).map((r) => (
                            <option key={r} value={r}>
                              {ROLE_LABELS[r]}
                            </option>
                          ))}
                        </select>
                        {m.user_id !== me?.id && (
                          <TooltipIconButton tooltip="Sacar del proyecto" onClick={() => void act(() => accountApi.removeMember(projectId, m.user_id))} className="size-7">
                            <XIcon />
                          </TooltipIconButton>
                        )}
                      </>
                    ) : (
                      <span className="text-xs text-muted-foreground">{ROLE_LABELS[m.role]}</span>
                    )}
                  </li>
                ))}
              </ul>
              {owner && (
                <form
                  className="flex gap-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void act(() => accountApi.addMember(projectId, email.trim(), role)).then((ok) => ok && setEmail(''));
                  }}
                >
                  <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="mail@empresa.com" required />
                  <select className={selectClass} value={role} onChange={(e) => setRole(e.target.value as ProjectRole)} aria-label="Rol">
                    {(Object.keys(ROLE_LABELS) as ProjectRole[]).map((r) => (
                      <option key={r} value={r} title={ROLE_HELP[r]}>
                        {ROLE_LABELS[r]}
                      </option>
                    ))}
                  </select>
                  <Button type="submit" disabled={busy || !email.trim()}>
                    Sumar
                  </Button>
                </form>
              )}
              <p className="text-xs text-muted-foreground">
                {(Object.keys(ROLE_LABELS) as ProjectRole[]).map((r) => `${ROLE_LABELS[r]}: ${ROLE_HELP[r].toLowerCase()}`).join(' · ')}
              </p>
            </section>

            {error && <p className="text-sm text-destructive">{error}</p>}

            <div className="flex flex-wrap justify-between gap-2 border-t pt-3">
              <Button variant="ghost" size="sm" onClick={() => void leave()} disabled={busy}>
                Salir del proyecto
              </Button>
              {owner &&
                (confirmArchive ? (
                  <span className="flex items-center gap-2 text-sm">
                    ¿Archivarlo? Sus dashboards vuelven a ser de quien los creó.
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={busy}
                      onClick={() =>
                        void act(async () => {
                          await accountApi.archiveProject(projectId);
                          useAppStore.getState().setProject(null);
                          await useAppStore.getState().loadMe();
                          onClose();
                        })
                      }
                    >
                      Archivar
                    </Button>
                  </span>
                ) : (
                  <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setConfirmArchive(true)}>
                    Archivar proyecto
                  </Button>
                ))}
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

// --- chats compartidos -------------------------------------------------------------------------

/** Lista de chats compartidos en el proyecto elegido (barra lateral). */
export function SharedChats() {
  const projectId = useAppStore((s) => s.projectId);
  const shares = useAppStore((s) => s.shares);
  const shareId = useAppStore((s) => s.shareId);
  const openShare = useAppStore((s) => s.openShare);
  if (!projectId) return null;
  return (
    <section>
      <h2 className="px-2 pb-1.5 text-xs font-medium text-muted-foreground">Chats compartidos</h2>
      {shares.length === 0 && <p className="px-2 text-xs text-muted-foreground">Nadie compartió chats en este proyecto.</p>}
      <ul className="flex flex-col gap-0.5">
        {shares.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => openShare(s.id)}
              className={cn(
                'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-start text-sm transition-colors hover:bg-sidebar-accent',
                s.id === shareId && 'bg-sidebar-accent font-medium',
              )}
            >
              <MessageSquareShareIcon className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 truncate">{s.title}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Botón del encabezado del chat: copia de solo lectura en un proyecto. */
export function ShareChatButton({ chatId }: { chatId: string | null }) {
  const me = useAppStore((s) => s.me);
  const [state, setState] = useState<{ done?: string; error?: string } | null>(null);
  const targets = me?.projects.filter((p) => p.role !== 'viewer') ?? [];
  if (!chatId || targets.length === 0) return null;
  const share = async (pid: string, name: string) => {
    try {
      await accountApi.shareChat(chatId, pid);
      setState({ done: `Compartido en ${name}` });
      if (useAppStore.getState().projectId === pid) void useAppStore.getState().loadShares();
    } catch (e) {
      setState({ error: (e as Error).message });
    }
    window.setTimeout(() => setState(null), 3000);
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className={cn('h-8 gap-1.5 px-2 text-xs', state?.error && 'text-destructive')}>
          {state?.done ? <CheckIcon className="size-3.5" /> : <MessageSquareShareIcon className="size-3.5" />}
          {state?.done ?? state?.error ?? 'Compartir'}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          Una copia de solo lectura de este chat, tal como está ahora
        </DropdownMenuLabel>
        {targets.map((p) => (
          <DropdownMenuItem key={p.id} onSelect={() => void share(p.id, p.name)}>
            <FolderIcon /> {p.name}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// Markdown de las respuestas, sin plugin de tipografía: lo mínimo para que se lea como en el chat.
const MD = [
  'text-sm leading-relaxed space-y-3',
  '[&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-base [&_h2]:font-semibold [&_h3]:font-semibold',
  '[&_ul]:list-disc [&_ul]:ps-5 [&_ol]:list-decimal [&_ol]:ps-5 [&_li]:my-0.5',
  '[&_a]:underline [&_a]:underline-offset-2 [&_strong]:font-semibold',
  '[&_code]:rounded [&_code]:bg-muted [&_code]:px-1 [&_code]:font-mono [&_code]:text-xs',
  '[&_pre]:overflow-x-auto [&_pre]:rounded-lg [&_pre]:bg-muted [&_pre]:p-3 [&_pre_code]:bg-transparent [&_pre_code]:p-0',
  '[&_table]:w-full [&_table]:text-xs [&_th]:border-b [&_th]:px-2 [&_th]:py-1 [&_th]:text-start [&_td]:border-b [&_td]:px-2 [&_td]:py-1',
  '[&_blockquote]:border-s-2 [&_blockquote]:ps-3 [&_blockquote]:text-muted-foreground',
].join(' ');

/** Copia compartida de un chat, de solo lectura (panel derecho). */
export function SharePanel({ shareId, onClose }: { shareId: string; onClose: () => void }) {
  const me = useAppStore((s) => s.me);
  const [share, setShare] = useState<Share | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setShare(null);
    accountApi.share(shareId).then(setShare).catch((e: Error) => setError(e.message));
  }, [shareId]);

  const remove = async () => {
    try {
      await accountApi.deleteShare(shareId);
      onClose();
      void useAppStore.getState().loadShares();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const blocks: ReactNode[] = [];
  share?.items.forEach((it, i) => {
    if (it.type === 'text' && it.role === 'user') {
      blocks.push(
        <div key={i} className="ms-auto max-w-[85%] rounded-2xl bg-muted px-3.5 py-2 text-sm whitespace-pre-wrap">
          {it.text}
        </div>,
      );
    } else if (it.type === 'text') {
      blocks.push(
        <div key={i} className={MD}>
          <Markdown remarkPlugins={[remarkGfm]}>{it.text}</Markdown>
        </div>,
      );
    } else if (it.type === 'tool_call') {
      blocks.push(
        <p key={i} className="text-xs text-muted-foreground">
          · {toolLabel(it.name, (it.args ?? {}) as Record<string, unknown>)}
        </p>,
      );
    }
  });

  return (
    <section className="flex h-full min-w-0 flex-col bg-background">
      <header className="flex h-12 shrink-0 items-center gap-1.5 border-b px-3">
        <MessageSquareShareIcon className="mx-1 size-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1 leading-tight">
          <h2 className="truncate text-sm font-medium">{share?.title ?? 'Chat compartido'}</h2>
          {share && (
            <p className="truncate text-[11px] text-muted-foreground">
              Compartido por {share.owner} {ago(share.created_at)} · solo lectura
            </p>
          )}
        </div>
        {share && me && (share.owner === me.id || me.projects.find((p) => p.id === share.project_id)?.role === 'owner') && (
          <Button variant="ghost" size="sm" className="h-7 text-xs text-muted-foreground" onClick={() => void remove()}>
            Dejar de compartir
          </Button>
        )}
        <TooltipIconButton tooltip="Cerrar" onClick={onClose} className="size-8">
          <XIcon />
        </TooltipIconButton>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <p className="p-4 text-sm text-destructive">{error}</p>
        ) : !share ? (
          <div className="flex flex-col gap-3 p-4">
            <Skeleton className="h-10 w-2/3 self-end" />
            <Skeleton className="h-24 w-full" />
          </div>
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-4 p-4">{blocks}</div>
        )}
      </div>
    </section>
  );
}

// --- dashboards: mover a un proyecto -----------------------------------------------------------------

/** Ítem del menú de un dashboard: moverlo a un proyecto o volverlo personal. */
export function MoveToProjectMenu({ artifactId, projectId, onMoved }: { artifactId: string; projectId: string | null; onMoved: () => void }) {
  const me = useAppStore((s) => s.me);
  const [error, setError] = useState<string | null>(null);
  const targets = me?.projects.filter((p) => p.role !== 'viewer' && p.id !== projectId) ?? [];
  if (!me || (targets.length === 0 && !projectId)) return null;
  const move = async (pid: string | null) => {
    try {
      await accountApi.moveArtifact(artifactId, pid);
      setError(null);
      onMoved();
      void useAppStore.getState().loadArtifacts();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <DropdownMenuSub>
      <DropdownMenuSubTrigger>
        <ArrowRightLeftIcon className="size-4 text-muted-foreground" /> {error ? <span className="text-destructive">{error}</span> : 'Mover a…'}
      </DropdownMenuSubTrigger>
      <DropdownMenuSubContent className="w-56">
        {projectId && (
          <DropdownMenuItem onSelect={() => void move(null)}>
            <UserIcon /> Personal
          </DropdownMenuItem>
        )}
        {targets.map((p) => (
          <DropdownMenuItem key={p.id} onSelect={() => void move(p.id)}>
            <FolderIcon /> {p.name}
          </DropdownMenuItem>
        ))}
      </DropdownMenuSubContent>
    </DropdownMenuSub>
  );
}

// --- cuenta y administración -------------------------------------------------------------------------

const THEMES: { value: ThemePref; label: string }[] = [
  { value: 'system', label: 'Sistema' },
  { value: 'light', label: 'Claro' },
  { value: 'dark', label: 'Oscuro' },
];

/** Pie de la barra lateral: quién sos, tema, usuarios (admins) y salir. */
export function UserMenu({ theme, onTheme }: { theme: ThemePref; onTheme: (t: ThemePref) => void }) {
  const me = useAppStore((s) => s.me);
  const [users, setUsers] = useState(false);
  const logout = async () => {
    await accountApi.logout().catch(() => undefined);
    location.href = '/login';
  };
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="h-10 w-full justify-start gap-2 px-2">
            {me ? <Avatar name={me.name} picture={me.picture} /> : <Skeleton className="size-6 rounded-full" />}
            <span className="min-w-0 flex-1 text-start leading-tight">
              <span className="block truncate text-sm">{me?.name ?? ''}</span>
              {me && me.name !== me.id && <span className="block truncate text-[11px] text-muted-foreground">{me.id}</span>}
            </span>
            <ChevronsUpDownIcon className="size-3.5 shrink-0 text-muted-foreground" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" side="top" className="w-60">
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>Tema</DropdownMenuSubTrigger>
            <DropdownMenuSubContent>
              <DropdownMenuRadioGroup value={theme} onValueChange={(v) => onTheme(v as ThemePref)}>
                {THEMES.map((t) => (
                  <DropdownMenuRadioItem key={t.value} value={t.value}>
                    {t.label}
                  </DropdownMenuRadioItem>
                ))}
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          {me?.admin && (
            <DropdownMenuItem onSelect={() => setUsers(true)}>
              <UsersIcon /> Usuarios
            </DropdownMenuItem>
          )}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void logout()}>
            <LogOutIcon /> Cerrar sesión
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {me?.admin && <UsersDialog open={users} onClose={() => setUsers(false)} self={me.id} />}
    </>
  );
}

function UsersDialog({ open, onClose, self }: { open: boolean; onClose: () => void; self: string }) {
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = () => accountApi.users().then(setUsers).catch((e: Error) => setError(e.message));
  useEffect(() => {
    if (open) {
      setError(null);
      void reload();
    }
  }, [open]);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await reload();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>Usuarios</DialogTitle>
          <DialogDescription>
            Entran con Google. Además de los dominios y mails habilitados en la configuración, entra quien invites acá.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void act(() => accountApi.invite(email.trim(), 'member')).then((ok) => ok && setEmail(''));
          }}
        >
          <Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Invitar: mail@empresa.com" required />
          <Button type="submit" disabled={busy || !email.trim()}>
            Invitar
          </Button>
        </form>
        {error && <p className="text-sm text-destructive">{error}</p>}
        {!users ? (
          <Skeleton className="h-40 w-full" />
        ) : (
          <ul className="flex max-h-80 flex-col overflow-y-auto">
            {users.map((u) => (
              <li key={u.id} className={cn('flex items-center gap-2 border-b py-2 last:border-0', u.status === 'disabled' && 'opacity-60')}>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-1.5 truncate text-sm">
                    {u.name || u.id}
                    {u.admin && (
                      <Badge variant="secondary" className="font-normal">
                        <ShieldIcon /> Admin
                      </Badge>
                    )}
                    {u.status === 'disabled' && (
                      <Badge variant="destructive" className="font-normal">
                        <TriangleAlertIcon /> Deshabilitado
                      </Badge>
                    )}
                  </span>
                  <span className="block truncate text-xs text-muted-foreground">
                    {u.name ? `${u.id} · ` : ''}
                    {u.last_login_at ? `entró ${ago(u.last_login_at)}` : 'todavía no entró'}
                  </span>
                </span>
                {u.id !== self && (
                  <>
                    <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={busy}
                      onClick={() => void act(() => accountApi.updateUser(u.id, { role: u.role === 'admin' ? 'member' : 'admin' }))}>
                      {u.role === 'admin' ? 'Quitar admin' : 'Hacer admin'}
                    </Button>
                    <Button variant="ghost" size="sm" className="h-7 text-xs" disabled={busy}
                      onClick={() => void act(() => accountApi.updateUser(u.id, { status: u.status === 'active' ? 'disabled' : 'active' }))}>
                      {u.status === 'active' ? 'Deshabilitar' : 'Habilitar'}
                    </Button>
                  </>
                )}
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Aviso cuando falta el permiso de BigQuery (las consultas van con tu cuenta). */
export function BigQueryConsentBanner() {
  const me = useAppStore((s) => s.me);
  if (!me || me.bigquery_ok) return null;
  return (
    <div className="flex items-center gap-2 border-b bg-amber-500/10 px-3 py-2 text-xs">
      <TriangleAlertIcon className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />
      <span className="min-w-0 flex-1">Las consultas a BigQuery van con tu cuenta y falta tu permiso.</span>
      <a className="font-medium underline underline-offset-2" href={`/auth/google?consent=1&next=${encodeURIComponent(location.pathname)}`}>
        Dar permiso
      </a>
    </div>
  );
}
