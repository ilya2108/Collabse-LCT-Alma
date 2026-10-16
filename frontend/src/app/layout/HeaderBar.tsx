import { useQuery } from '@tanstack/react-query';
import { Bell, ChevronDown, Eye, KeyRound, LogOut, Menu } from 'lucide-react';
import { useMemo, type ReactNode } from 'react';
import { Link, matchPath, useLocation, useNavigate } from 'react-router-dom';
import { breadcrumbsFor, type Crumb } from '@/app/menuConfig';
import { NotificationsBell } from '@/app/layout/NotificationsBell';
import { getAdminRole } from '@/shared/api/endpoints/roles';
import type { RecordScope, WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { Avatar, AvatarFallback } from '@/shared/ui/avatar';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/shared/ui/breadcrumb';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { SearchCommand } from '@/shared/ui/search-command';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

function initials(fullName: string): string {
  return fullName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
}

/**
 * Название роли для последней крошки «Администрирование → Роли и права → <роль>»:
 * берётся из кэша карточки роли (запрос делает сам редактор, здесь — только подписка).
 */
function useRoleCrumb(pathname: string, crumbs: Crumb[]): Crumb[] {
  const match = matchPath('/admin/roles/:roleId', pathname);
  const roleId = match?.params.roleId && match.params.roleId !== 'new' ? match.params.roleId : '';
  const roleQuery = useQuery({
    queryKey: ['admin-role', roleId],
    queryFn: ({ signal }) => getAdminRole(roleId, signal),
    enabled: false,
  });
  const name = roleQuery.data?.name;
  if (!roleId || !name || crumbs.length === 0) return crumbs;
  return [...crumbs.slice(0, -1), { title: name }];
}

/** Процессы и область записей пользователя одной строкой: «B2B, B2C (только свои)». */
function accessSummary(
  workflowTypes: WorkflowType[],
  recordsScope: (wf: WorkflowType) => RecordScope | null,
): string {
  if (workflowTypes.length === 0) return 'заявки недоступны';
  return workflowTypes
    .map((wf) => `${WORKFLOW_TYPE_LABELS[wf]}${recordsScope(wf) === 'own' ? ' (только свои)' : ''}`)
    .join(', ');
}

interface HeaderBarProps {
  /** Бургер мобильного сайдбара (< lg): открывает Sheet в AppLayout. */
  showMenuButton?: boolean;
  onOpenMenu?: () => void;
  /**
   * Слот кнопки перезапуска тура `CircleHelp` (§6.2, §7.6) — сам онбординг
   * делает WP6: AppLayout передаёт сюда DropdownMenu туров.
   */
  helpSlot?: ReactNode;
}

/**
 * Хедер 56px (redesign.md §6.2): бургер (планшет), крошки, поиск Cmd+K,
 * слот помощи/туров, колокольчик, тег «Режим просмотра», чип роли и меню профиля.
 * Роли и режим просмотра — из /auth/me: после access.changed перестраиваются сами.
 */
export function HeaderBar({ showMenuButton = false, onOpenMenu, helpSlot }: HeaderBarProps): ReactNode {
  const { user, roleNames, readOnly, workflowTypes, recordsScope, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const baseCrumbs = useMemo(() => breadcrumbsFor(location.pathname), [location.pathname]);
  const crumbs = useRoleCrumb(location.pathname, baseCrumbs);
  const mainRoleName = roleNames[0] ?? null;
  const extraRoles = roleNames.length - 1;

  return (
    <header className="sticky top-0 z-20 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-4 lg:px-6">
      {showMenuButton ? (
        <Button variant="ghost" size="icon" aria-label="Открыть меню" onClick={onOpenMenu}>
          <Menu aria-hidden="true" />
        </Button>
      ) : null}

      {crumbs.length > 0 ? (
        <Breadcrumb className="hidden min-w-0 md:block">
          <BreadcrumbList className="flex-nowrap">
            {crumbs.map((crumb, index) => (
              <BreadcrumbItem key={`${crumb.title}-${index}`} className="min-w-0">
                {index > 0 ? <BreadcrumbSeparator /> : null}
                {crumb.href ? (
                  <BreadcrumbLink asChild>
                    <Link to={crumb.href} className="truncate">
                      {crumb.title}
                    </Link>
                  </BreadcrumbLink>
                ) : (
                  <BreadcrumbPage className="truncate">{crumb.title}</BreadcrumbPage>
                )}
              </BreadcrumbItem>
            ))}
          </BreadcrumbList>
        </Breadcrumb>
      ) : null}

      <div className="ml-auto flex min-w-0 items-center gap-1.5">
        {readOnly ? (
          <span
            data-tour="header-observer-badge"
            className="mr-1 hidden items-center gap-1.5 rounded-sm bg-status-draft-tint px-2 py-0.5 text-xs font-medium text-status-draft-deep lg:inline-flex"
          >
            <Eye className="size-3" aria-hidden="true" />
            Режим просмотра
          </span>
        ) : null}
        {mainRoleName ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span
                data-tour="header-role-chip"
                tabIndex={0}
                className="mr-1 hidden max-w-[220px] items-center gap-1.5 rounded-sm bg-primary-tint px-2 py-0.5 text-xs font-medium text-primary outline-none focus-visible:ring-2 focus-visible:ring-ring md:inline-flex"
              >
                <KeyRound className="size-3 shrink-0" aria-hidden="true" />
                <span className="min-w-0 truncate">{mainRoleName}</span>
                {extraRoles > 0 ? <span className="tabular shrink-0">+{extraRoles}</span> : null}
              </span>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs">
              <span className="block font-medium">Роли: {roleNames.join(', ')}</span>
              <span className="block">Процессы: {accessSummary(workflowTypes, recordsScope)}</span>
            </TooltipContent>
          </Tooltip>
        ) : null}
        <SearchCommand />
        {helpSlot}
        <NotificationsBell />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="flex items-center gap-2 rounded-md px-1.5 py-1 outline-none transition-colors duration-150 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              aria-label="Меню профиля"
            >
              <Avatar className="size-8">
                <AvatarFallback className="bg-primary text-xs font-semibold text-primary-foreground">
                  {user ? initials(user.full_name) : '…'}
                </AvatarFallback>
              </Avatar>
              <span className="hidden min-w-0 text-left leading-tight xl:block">
                <span className="block max-w-40 truncate text-[13px] font-semibold">
                  {user?.full_name ?? '…'}
                </span>
                {mainRoleName ? (
                  <span className="block max-w-40 truncate text-xs text-muted-foreground">
                    {mainRoleName}
                    {extraRoles > 0 ? ` +${extraRoles}` : ''}
                  </span>
                ) : null}
              </span>
              <ChevronDown className="hidden size-3.5 text-muted-foreground xl:block" aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuLabel className="font-normal">
              <span className="block truncate text-sm font-semibold">{user?.full_name ?? '…'}</span>
              <span className="block text-xs text-muted-foreground">
                {roleNames.length > 0 ? roleNames.join(', ') : 'Роли не назначены'}
              </span>
              <span className="block text-xs text-muted-foreground">
                Процессы: {accessSummary(workflowTypes, recordsScope)}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => navigate('/settings/notifications')}>
              <Bell aria-hidden="true" />
              Настройки нотификаций
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => logout()}>
              <LogOut aria-hidden="true" />
              Выйти
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
