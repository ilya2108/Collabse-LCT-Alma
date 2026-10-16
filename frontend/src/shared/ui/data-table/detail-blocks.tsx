import { ChevronRight, type LucideIcon } from 'lucide-react';
import { motion } from 'motion/react';
import type { CSSProperties, ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/shared/lib/cn';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';

/**
 * Блоки детальных страниц (redesign.md §6.5): карточка-секция вместо AntD Card,
 * DL-сетка реквизитов вместо Descriptions-простыни (§5.1), мини-карточки
 * связей как в §6.4 и stagger-вход карточек-обзоров (§2.3).
 */

interface SectionCardProps {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function SectionCard({ title, extra, children, className }: SectionCardProps): ReactNode {
  return (
    <section className={cn('min-w-0 rounded-lg border bg-card p-4 shadow-card sm:p-5', className)}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}

/**
 * DL-сетка §5.1: `dt` — text-muted-foreground, значения — text-sm.
 * < sm (P1-5): подпись над значением — колонка подписей 160–180 px оставляла
 * значению на 375 px меньше половины ширины, длинные номера и e-mail вылезали.
 */
export function DL({
  children,
  labelWidth = 160,
}: {
  children: ReactNode;
  labelWidth?: number;
}): ReactNode {
  return (
    <dl
      className="grid grid-cols-1 gap-y-0.5 text-sm sm:grid-cols-[var(--dl-label)_minmax(0,1fr)] sm:gap-y-2"
      style={{ '--dl-label': `${labelWidth}px` } as CSSProperties}
    >
      {children}
    </dl>
  );
}

export function DLRow({ label, children }: { label: string; children: ReactNode }): ReactNode {
  return (
    <>
      <dt className="text-xs text-muted-foreground sm:pr-4 sm:text-sm">{label}</dt>
      <dd className="mb-2 min-w-0 break-words last:mb-0 sm:mb-0">{children}</dd>
    </>
  );
}

interface MiniCardProps {
  to: string;
  icon: LucideIcon;
  title: ReactNode;
  description?: ReactNode;
  extra?: ReactNode;
}

/** Мини-карточка связи (§6.4/§6.5): иконка + две строки + стрелка. */
export function MiniCard({ to, icon: Icon, title, description, extra }: MiniCardProps): ReactNode {
  return (
    <Link
      to={to}
      className="group flex items-center gap-3 rounded-md border bg-card px-3 py-2.5 transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:shadow-overlay"
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary-tint text-primary" aria-hidden="true">
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground group-hover:text-primary">
          {title}
        </span>
        {description ? (
          <span className="block truncate text-xs text-muted-foreground">{description}</span>
        ) : null}
      </span>
      {extra}
      <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
    </Link>
  );
}

/** Обёртка stagger-входа карточек-обзоров (§2.3, ≤12 элементов). */
export function StaggerGrid({
  children,
  className,
  'data-tour': dataTour,
}: {
  children: ReactNode;
  className?: string;
  /** Цель онбординг-тура §7.3. */
  'data-tour'?: string;
}): ReactNode {
  return (
    <motion.div
      className={className}
      data-tour={dataTour}
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
    >
      {children}
    </motion.div>
  );
}

export function StaggerItem({ children, className }: { children: ReactNode; className?: string }): ReactNode {
  return (
    <motion.div className={className} variants={staggerItem}>
      {children}
    </motion.div>
  );
}
