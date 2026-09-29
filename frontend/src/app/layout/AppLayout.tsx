import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Outlet } from 'react-router-dom';
import { AppSidebar } from '@/app/layout/AppSidebar';
import { RestartTourButton } from '@/features/onboarding';
import { cn } from '@/shared/lib/cn';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { PageTransition } from '@/shared/ui/page-transition';
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/shared/ui/sheet';
import { HeaderBar } from './HeaderBar';
import { SiderLogo } from './SiderLogo';

/**
 * Каркас приложения (redesign.md §6.2): тёмно-синий сайдбар 248px
 * (свёрнутый 64px — иконки с тултипами), хедер 56px, контент max-width 1440
 * с PageTransition вокруг <Outlet/>. Адаптив §8.1: lg–xl — сайдбар в иконки,
 * < lg — сайдбар в Sheet по бургеру. Маршруты и guard'ы не меняются.
 */
export function AppLayout(): ReactNode {
  const { lg, xl } = useBreakpoint();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  // < lg (1024) — сайдбар прячется в Sheet; lg…xl — принудительно иконки (§8.1).
  const sheetMode = !lg;
  const effectiveCollapsed = collapsed || (lg && !xl);

  return (
    <div className="flex min-h-screen bg-background">
      {sheetMode ? null : (
        <aside
          className={cn(
            'sticky top-0 flex h-screen shrink-0 flex-col bg-sidebar',
            effectiveCollapsed ? 'w-16' : 'w-[248px]',
          )}
        >
          <SiderLogo collapsed={effectiveCollapsed} />
          <div className="min-h-0 flex-1 overflow-y-auto">
            <AppSidebar collapsed={effectiveCollapsed} layoutIdSuffix="desktop" />
          </div>
          {xl ? (
            <div className={cn('border-t border-white/10 p-2', effectiveCollapsed && 'flex justify-center')}>
              <button
                type="button"
                onClick={() => setCollapsed((prev) => !prev)}
                aria-label={effectiveCollapsed ? 'Развернуть меню' : 'Свернуть меню'}
                className={cn(
                  'flex min-h-10 items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-muted outline-none',
                  'transition-colors duration-150 hover:bg-white/5 hover:text-white',
                  'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar',
                  effectiveCollapsed ? 'justify-center px-0' : 'w-full',
                )}
              >
                {effectiveCollapsed ? (
                  <PanelLeftOpen className="size-[18px]" aria-hidden="true" />
                ) : (
                  <>
                    <PanelLeftClose className="size-[18px]" aria-hidden="true" />
                    <span>Свернуть</span>
                  </>
                )}
              </button>
            </div>
          ) : null}
        </aside>
      )}

      {sheetMode ? (
        <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
          <SheetContent
            side="left"
            className="flex w-[280px] flex-col border-0 bg-sidebar p-0 text-sidebar-foreground [&>button]:text-sidebar-muted"
          >
            <SheetHeader className="sr-only">
              <SheetTitle>Навигация</SheetTitle>
              <SheetDescription>Разделы системы</SheetDescription>
            </SheetHeader>
            <SiderLogo collapsed={false} />
            <div className="min-h-0 flex-1 overflow-y-auto">
              <AppSidebar layoutIdSuffix="mobile" onNavigate={() => setMobileOpen(false)} />
            </div>
          </SheetContent>
        </Sheet>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <HeaderBar
          showMenuButton={sheetMode}
          onOpenMenu={() => setMobileOpen(true)}
          helpSlot={<RestartTourButton />}
        />
        <main className="min-w-0 flex-1">
          <div className="app-content">
            <PageTransition>
              <Outlet />
            </PageTransition>
          </div>
        </main>
      </div>
    </div>
  );
}
