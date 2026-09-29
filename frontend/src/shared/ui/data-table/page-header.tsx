import { ArrowLeft } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';

/**
 * Заголовок экрана нового кита (redesign.md §3.3). Вход — в общем
 * PageTransition, без собственной анимации. Локальная реализация WP2
 * по замороженному контракту §3.3 (общий компонент — за WP владельца).
 */
export interface PageHeaderProps {
  /** H1 20/28 semibold, text-balance. */
  title: string;
  /** 1 строка text-muted-foreground; НЕ абзацы. */
  subtitle?: ReactNode;
  /** Действия справа (primary-кнопка экрана). */
  extra?: ReactNode;
  /** Стрелка назад (детальные страницы). */
  backTo?: string;
  /** Строка бейджей/фактов под заголовком (StatusBadge, даты). */
  meta?: ReactNode;
}

export function PageHeader({ title, subtitle, extra, backTo, meta }: PageHeaderProps): ReactNode {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
      <div className="flex min-w-0 items-start gap-2">
        {backTo ? (
          <Link
            to={backTo}
            aria-label="Назад"
            className="mt-0.5 inline-flex size-8 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-[color,background-color] duration-150 hover:bg-accent hover:text-accent-foreground"
          >
            <ArrowLeft className="size-4" aria-hidden="true" />
          </Link>
        ) : null}
        <div className="min-w-0">
          <h1 className="text-balance text-xl font-semibold">{title}</h1>
          {subtitle ? <div className="mt-0.5 text-sm text-muted-foreground">{subtitle}</div> : null}
          {meta ? <div className="mt-1.5 flex flex-wrap items-center gap-2">{meta}</div> : null}
        </div>
      </div>
      {extra ? <div className="flex flex-wrap items-center gap-2">{extra}</div> : null}
    </div>
  );
}
