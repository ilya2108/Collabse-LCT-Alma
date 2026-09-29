import { ChevronRight, type LucideIcon } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
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
    <section className={cn('rounded-lg border bg-card p-5 shadow-card', className)}>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{title}</h2>
        {extra}
      </div>
      {children}
    </section>
  );
}

/** DL-сетка §5.1: `dt` — text-muted-foreground, значения — text-sm. */
export function DL({
  children,
  labelWidth = 160,
}: {
  children: ReactNode;
  labelWidth?: number;
}): ReactNode {
  return (
    <dl className="grid gap-y-2 text-sm" style={{ gridTemplateColumns: `${labelWidth}px 1fr` }}>
      {children}
    </dl>
  );
}

export function DLRow({ label, children }: { label: string; children: ReactNode }): ReactNode {
  return (
    <>
      <dt className="pr-4 text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
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
