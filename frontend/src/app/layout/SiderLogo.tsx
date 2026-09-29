import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/shared/lib/cn';

/** Логотип в сайдбаре (§6.2): полный «Альма» или компактный знак. */
export function SiderLogo({ collapsed }: { collapsed: boolean }): ReactNode {
  return (
    <Link
      to="/dashboard"
      aria-label="Альма — к аналитике"
      className={cn(
        'flex h-14 shrink-0 items-center gap-2.5 text-white outline-none',
        'focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar',
        collapsed ? 'justify-center px-0' : 'px-5',
      )}
    >
      <img src="/favicon.svg" alt="" width={28} height={28} className="block" />
      {collapsed ? null : (
        <span className="whitespace-nowrap text-base font-semibold">Альма</span>
      )}
    </Link>
  );
}
