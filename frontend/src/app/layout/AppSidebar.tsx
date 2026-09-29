import {
  Bell,
  BookOpen,
  BookText,
  Building2,
  ChevronDown,
  Circle,
  ClipboardCheck,
  FileSignature,
  FileText,
  FileUp,
  Flag,
  GraduationCap,
  LayoutDashboard,
  Library,
  Package,
  Plug,
  ScrollText,
  Shield,
  SquareKanban,
  Users,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import { motion } from 'motion/react';
import { useMemo, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router-dom';
import {
  activeMenuKeys,
  buildMenuEntries,
  isNavGroup,
  type NavEntry,
  type NavItem,
} from '@/app/menuConfig';
import { useAuth } from '@/shared/auth/AuthContext';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * Навигация сайдбара (redesign.md §6.2): свой nav на menuConfig (замена
 * Menu AntD), иконки lucide 18px, активный пункт — скользящая подложка
 * `layoutId` (springs.snappy), группы — Collapsible, свёрнутый режим —
 * иконки с Tooltip side="right". menuConfig не меняется (Р7) — маппинг
 * пунктов на lucide-иконки живёт здесь.
 */

const NAV_ICONS: Record<string, LucideIcon> = {
  dashboard: LayoutDashboard,
  board: SquareKanban,
  registry: Library,
  'registry-universities': Building2,
  'registry-contracts': FileSignature,
  'registry-products': Package,
  'registry-programs': BookOpen,
  'talent-pool': GraduationCap,
  workflow: Workflow,
  import: FileUp,
  notifications: Bell,
  admin: Shield,
  'admin-users': Users,
  'admin-dictionaries': BookText,
  'admin-feature-flags': Flag,
  'admin-approvals': ClipboardCheck,
  'admin-integrations': Plug,
  'admin-audit': ScrollText,
  requests: FileText,
};

function iconFor(key: string): LucideIcon {
  return NAV_ICONS[key] ?? Circle;
}

interface AppSidebarProps {
  collapsed?: boolean;
  /** Уникальный суффикс layoutId — сайдбар и мобильный Sheet не конфликтуют. */
  layoutIdSuffix: string;
  /** Закрыть мобильный Sheet после перехода. */
  onNavigate?: () => void;
}

interface NavLinkItemProps {
  item: NavItem;
  active: boolean;
  collapsed: boolean;
  layoutId: string;
  onNavigate?: () => void;
  nested?: boolean;
}

function NavLinkItem({
  item,
  active,
  collapsed,
  layoutId,
  onNavigate,
  nested = false,
}: NavLinkItemProps): ReactNode {
  const Icon = iconFor(item.key);
  const link = (
    <Link
      to={item.path}
      onClick={onNavigate}
      data-tour={item.key === 'import' ? 'nav-import' : undefined}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'relative flex min-h-10 items-center gap-3 rounded-md px-3 py-2 text-sm outline-none',
        'transition-colors duration-150 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar',
        active ? 'text-white' : 'text-sidebar-muted hover:bg-white/5 hover:text-white',
        collapsed && 'justify-center px-0',
      )}
    >
      {active ? (
        <motion.span
          layoutId={layoutId}
          transition={springs.snappy}
          className="absolute inset-0 rounded-md bg-sidebar-active"
          aria-hidden="true"
        />
      ) : null}
      <Icon className="relative z-10 size-[18px] shrink-0" aria-hidden="true" />
      {collapsed ? null : (
        <span className={cn('relative z-10 min-w-0 flex-1 truncate', nested && 'text-[13px]')}>
          {item.label}
        </span>
      )}
    </Link>
  );

  if (!collapsed) return link;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{link}</TooltipTrigger>
      <TooltipContent side="right">{item.label}</TooltipContent>
    </Tooltip>
  );
}

export function AppSidebar({
  collapsed = false,
  layoutIdSuffix,
  onNavigate,
}: AppSidebarProps): ReactNode {
  const { roles } = useAuth();
  const location = useLocation();

  const entries = useMemo<NavEntry[]>(() => buildMenuEntries(roles), [roles]);
  const { selected, open } = useMemo(() => activeMenuKeys(location.pathname), [location.pathname]);
  const selectedKey = selected[0] ?? null;
  const layoutId = `nav-active-${layoutIdSuffix}`;

  // Открытые группы: активная — открыта по умолчанию, состояние — вручную.
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>(() =>
    Object.fromEntries(open.map((key) => [key, true])),
  );

  return (
    <nav aria-label="Основная навигация" data-tour="nav" className="flex flex-col gap-0.5 px-2 py-3">
      {entries.map((entry) => {
        if (!isNavGroup(entry)) {
          return (
            <NavLinkItem
              key={entry.key}
              item={entry}
              active={selectedKey === entry.key}
              collapsed={collapsed}
              layoutId={layoutId}
              onNavigate={onNavigate}
            />
          );
        }

        // Свёрнутый сайдбар: группа разворачивается в иконки детей (§6.2).
        if (collapsed) {
          return entry.children.map((child) => (
            <NavLinkItem
              key={child.key}
              item={child}
              active={selectedKey === child.key}
              collapsed
              layoutId={layoutId}
              onNavigate={onNavigate}
            />
          ));
        }

        const GroupIcon = iconFor(entry.key);
        const groupOpen = openGroups[entry.key] ?? open.includes(entry.key);
        return (
          <Collapsible
            key={entry.key}
            open={groupOpen}
            onOpenChange={(next) => setOpenGroups((prev) => ({ ...prev, [entry.key]: next }))}
          >
            <CollapsibleTrigger asChild>
              <button
                type="button"
                data-tour={entry.key === 'registry' ? 'nav-registry' : undefined}
                className={cn(
                  'flex min-h-10 w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-muted outline-none',
                  'transition-colors duration-150 hover:bg-white/5 hover:text-white',
                  'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar',
                )}
              >
                <GroupIcon className="size-[18px] shrink-0" aria-hidden="true" />
                <span className="min-w-0 flex-1 truncate text-left">{entry.label}</span>
                <ChevronDown
                  aria-hidden="true"
                  className={cn(
                    'size-4 shrink-0 transition-transform duration-150',
                    groupOpen && 'rotate-180',
                  )}
                />
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="ml-4 flex flex-col gap-0.5 border-l border-white/10 py-0.5 pl-2">
                {entry.children.map((child) => (
                  <NavLinkItem
                    key={child.key}
                    item={child}
                    active={selectedKey === child.key}
                    collapsed={false}
                    layoutId={layoutId}
                    onNavigate={onNavigate}
                    nested
                  />
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        );
      })}
    </nav>
  );
}
