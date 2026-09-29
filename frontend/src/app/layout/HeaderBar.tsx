import { Bell, ChevronDown, Eye, LogOut, Menu } from 'lucide-react';
import { useMemo, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { breadcrumbsFor } from '@/app/menuConfig';
import { NotificationsBell } from '@/app/layout/NotificationsBell';
import { useAuth } from '@/shared/auth/AuthContext';
import { ROLE_LABELS } from '@/shared/auth/roles';
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
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { SearchCommand } from '@/shared/ui/search-command';

function initials(fullName: string): string {
  return fullName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('');
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
 * слот помощи/туров, колокольчик, бейдж observer и меню профиля.
 */
export function HeaderBar({ showMenuButton = false, onOpenMenu, helpSlot }: HeaderBarProps): ReactNode {
  const { user, mainRole, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();

  const crumbs = useMemo(() => breadcrumbsFor(location.pathname), [location.pathname]);

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

      <div className="ml-auto flex items-center gap-1.5">
        {mainRole === 'observer' ? (
          <span
            data-tour="header-observer-badge"
            className="mr-1 hidden items-center gap-1.5 rounded-sm bg-status-draft-tint px-2 py-0.5 text-xs font-medium text-status-draft-deep lg:inline-flex"
          >
            <Eye className="size-3" aria-hidden="true" />
            Режим просмотра
          </span>
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
                {mainRole ? (
                  <span className="block text-xs text-muted-foreground">
                    {ROLE_LABELS[mainRole]}
                  </span>
                ) : null}
              </span>
              <ChevronDown className="hidden size-3.5 text-muted-foreground xl:block" aria-hidden="true" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
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
