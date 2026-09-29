import { Flame } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SemanticStatus } from '@/shared/config/tokens';
import { StatusBadge, StageTag } from '@/shared/ui/data-table';

/**
 * Совместимость WP4 (стадия Integrate): локальные бейджи заменены обёртками
 * над общим StatusBadge/StageTag (redesign.md §3.6, shared/ui/data-table).
 */

/** Бейдж семантического статуса: точка/иконка + текст (§3.6). */
export function SemanticBadge({
  status,
  icon,
  children,
  className,
}: {
  status: SemanticStatus;
  icon?: LucideIcon;
  children: ReactNode;
  className?: string;
}): ReactNode {
  return (
    <StatusBadge status={status} icon={icon} className={className}>
      {children}
    </StatusBadge>
  );
}

/** Чип этапа заявки: произвольный цвет этапа через color-mix (§1.2). */
export function StageChip({
  name,
  color,
  className,
}: {
  name: string;
  color?: string | null;
  className?: string;
}): ReactNode {
  return (
    <span className={className}>
      <StageTag name={name} color={color} />
    </span>
  );
}

/** Бейдж «зависла»: Flame + N дн. без движения (§3.6). */
export function StuckBadge({ days, short = false }: { days: number; short?: boolean }): ReactNode {
  return (
    <StatusBadge status="warning" icon={Flame}>
      {short ? `${days} дн.` : `${days} дн. без движения`}
    </StatusBadge>
  );
}
