import { Lock } from 'lucide-react';
import type { ReactNode } from 'react';
import type { RecordScope, WorkflowType } from '@/shared/api/types';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { StatusBadge } from '@/shared/ui/data-table';

/** Мелкие плашки экранов «Роли и права»: процессы, область записей, тип роли. */

/** Чипы процессов роли: B2B · B2C. */
export function WorkflowChips({
  workflowTypes,
  className,
}: {
  workflowTypes: readonly WorkflowType[];
  className?: string;
}): ReactNode {
  return (
    <span className={cn('inline-flex flex-wrap items-center gap-1', className)}>
      {workflowTypes.map((wf) => (
        <span
          key={wf}
          className="rounded-sm bg-muted px-1.5 py-px text-[11px] font-medium text-foreground"
        >
          {WORKFLOW_TYPE_LABELS[wf]}
        </span>
      ))}
    </span>
  );
}

/** Пометка права с областью: действует только в процессах роли. */
export function ScopedChip({
  workflowTypes,
}: {
  workflowTypes: readonly WorkflowType[];
}): ReactNode {
  const label =
    workflowTypes.length > 0
      ? workflowTypes.map((wf) => WORKFLOW_TYPE_LABELS[wf]).join(' · ')
      : 'процессы не выбраны';
  return (
    <span
      title="Право действует только в процессах роли"
      className="inline-flex w-fit items-center gap-1 rounded-sm bg-status-progress-tint px-1.5 py-px text-[11px] font-medium text-status-progress-deep"
    >
      в процессах роли: {label}
    </span>
  );
}

/** «Все» / «Только свои». */
export function RecordScopeLabel({ scope }: { scope: RecordScope }): ReactNode {
  return scope === 'own' ? (
    <StatusBadge status="warning" size="sm">
      Только свои
    </StatusBadge>
  ) : (
    <span className="text-sm">Все</span>
  );
}

/** Бейдж типа роли: системная (с замком у заблокированной admin) или своя. */
export function RoleTypeBadge({
  isSystem,
  isLocked,
}: {
  isSystem: boolean;
  isLocked?: boolean;
}): ReactNode {
  return (
    <span className="inline-flex items-center gap-1">
      <StatusBadge status={isSystem ? 'progress' : 'talent'} size="sm">
        {isSystem ? 'Системная' : 'Своя'}
      </StatusBadge>
      {isLocked ? (
        <Lock
          className="size-3.5 text-muted-foreground"
          aria-label="Заблокирована: права не редактируются"
        />
      ) : null}
    </span>
  );
}
