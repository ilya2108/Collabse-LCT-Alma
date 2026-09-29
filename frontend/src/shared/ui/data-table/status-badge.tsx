import type { LucideIcon } from 'lucide-react';
import type { CSSProperties, ReactNode } from 'react';
import type { ContractStatus } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { stageText, stageTint, type SemanticStatus } from '@/shared/config/tokens';

/**
 * StatusBadge — замена AntD Tag (redesign.md §3.6). Фон tint, текст deep,
 * слева точка 6px core-цвета или иконка: статус никогда не только цветом.
 * Для произвольного цвета этапа workflow — color-mix формула §1.2.
 */

export interface StatusBadgeProps {
  /** Семантические тройки из §1.2. */
  status?: SemanticStatus;
  /** ИЛИ произвольный цвет этапа workflow (hex из конфига схемы). */
  color?: string;
  /** Иконка 12px вместо точки. */
  icon?: LucideIcon;
  children: ReactNode;
  size?: 'sm' | 'md';
  className?: string;
}

const STATUS_CLASSES: Record<SemanticStatus, { bg: string; text: string; dot: string }> = {
  draft: { bg: 'bg-status-draft-tint', text: 'text-status-draft-deep', dot: 'bg-status-draft' },
  progress: { bg: 'bg-status-progress-tint', text: 'text-status-progress-deep', dot: 'bg-status-progress' },
  success: { bg: 'bg-status-success-tint', text: 'text-status-success-deep', dot: 'bg-status-success' },
  warning: { bg: 'bg-status-warning-tint', text: 'text-status-warning-deep', dot: 'bg-status-warning' },
  danger: { bg: 'bg-status-danger-tint', text: 'text-status-danger-deep', dot: 'bg-status-danger' },
  talent: { bg: 'bg-status-talent-tint', text: 'text-status-talent-deep', dot: 'bg-status-talent' },
};

export function StatusBadge({
  status,
  color,
  icon: Icon,
  children,
  size = 'md',
  className,
}: StatusBadgeProps): ReactNode {
  const semantic = status ? STATUS_CLASSES[status] : null;
  const custom: CSSProperties | undefined =
    !semantic && color ? { backgroundColor: stageTint(color), color: stageText(color) } : undefined;
  const dotStyle: CSSProperties | undefined = !semantic && color ? { backgroundColor: color } : undefined;
  return (
    <span
      className={cn(
        'inline-flex w-fit shrink-0 items-center gap-1.5 whitespace-nowrap rounded-sm font-medium',
        size === 'sm' ? 'px-1.5 py-px text-[11px]' : 'px-2 py-0.5 text-xs',
        semantic ? [semantic.bg, semantic.text] : !color && 'bg-muted text-muted-foreground',
        className,
      )}
      style={custom}
    >
      {Icon ? (
        <Icon className="size-3 shrink-0" aria-hidden="true" />
      ) : (
        <span
          className={cn('size-1.5 shrink-0 rounded-full', semantic?.dot)}
          style={dotStyle}
          aria-hidden="true"
        />
      )}
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}

/** Тег этапа заявки: цвет — из схемы workflow (совместим с прежним StageTag). */
export function StageTag({
  name,
  color,
  size,
}: {
  name: string;
  color?: string | null;
  size?: 'sm' | 'md';
}): ReactNode {
  return color ? (
    <StatusBadge color={color} size={size}>
      {name}
    </StatusBadge>
  ) : (
    <StatusBadge status="progress" size={size}>
      {name}
    </StatusBadge>
  );
}

const CONTRACT_STATUS_META: Record<ContractStatus, { label: string; status: SemanticStatus }> = {
  draft: { label: 'Черновик', status: 'draft' },
  negotiation: { label: 'Переговоры', status: 'progress' },
  active: { label: 'Действует', status: 'success' },
  completed: { label: 'Завершён', status: 'draft' },
  terminated: { label: 'Расторгнут', status: 'danger' },
};

/** Бейдж статуса договора (ux.md §9.2, словарь прежнего StatusTag). */
export function ContractStatusTag({ status, size }: { status: string; size?: 'sm' | 'md' }): ReactNode {
  const meta = CONTRACT_STATUS_META[status as ContractStatus];
  return (
    <StatusBadge status={meta?.status ?? 'draft'} size={size}>
      {meta?.label ?? status}
    </StatusBadge>
  );
}
