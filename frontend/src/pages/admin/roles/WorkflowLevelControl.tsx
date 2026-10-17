import { useId, type ReactNode } from 'react';
import type { WorkflowType } from '@/shared/api/types';
import {
  P,
  WORKFLOW_LEVELS,
  WORKFLOW_LEVEL_HINTS,
  WORKFLOW_LEVEL_LABELS,
  workflowLevelOf,
  workflowLevelPermissions,
  type WorkflowLevel,
} from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { Checkbox } from '@/shared/ui/checkbox';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import { ScopedChip } from './RoleChips';

/**
 * Уровни конструктора процессов (Р-F2-7) вместо четырёх флажков workflow:*:
 * «Нет доступа / Просмотр / Правка с согласованием / Правка и публикация»
 * и флажок «История изменений» (для уровней правки обязателен).
 * Права уровня действуют только в процессах роли.
 */

interface WorkflowLevelControlProps {
  selected: ReadonlySet<string>;
  /** Новый набор прав workflow:* (остальные права роли не трогаются). */
  onChange: (workflowPermissions: string[], level: WorkflowLevel) => void;
  workflowTypes: WorkflowType[];
  disabled?: boolean;
}

export function WorkflowLevelControl({
  selected,
  onChange,
  workflowTypes,
  disabled = false,
}: WorkflowLevelControlProps): ReactNode {
  const historyId = useId();
  const level = workflowLevelOf((code) => selected.has(code));
  const history = selected.has(P.WORKFLOW_HISTORY);
  const historyLocked = level === 'change' || level === 'approve';

  return (
    <div className="space-y-3">
      <RadioGroup
        value={level}
        disabled={disabled}
        aria-label="Уровень доступа к конструктору процессов"
        onValueChange={(value) => {
          const next = value as WorkflowLevel;
          onChange(workflowLevelPermissions(next, history), next);
        }}
        className="grid gap-2 sm:grid-cols-2"
      >
        {WORKFLOW_LEVELS.map((item) => (
          <label
            key={item}
            className={cn(
              'flex items-start gap-2 rounded-md border p-2.5 text-sm transition-colors duration-150',
              level === item ? 'border-primary bg-primary-tint' : 'border-border',
              disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer hover:bg-accent',
            )}
          >
            <RadioGroupItem value={item} className="mt-0.5" />
            <span className="min-w-0">
              <span className="block font-medium">{WORKFLOW_LEVEL_LABELS[item]}</span>
              <span className="block text-xs text-muted-foreground">
                {WORKFLOW_LEVEL_HINTS[item]}
              </span>
            </span>
          </label>
        ))}
      </RadioGroup>

      <label
        htmlFor={historyId}
        className={cn(
          'flex items-start gap-2 text-sm',
          disabled || level === 'none' || historyLocked ? 'cursor-not-allowed' : 'cursor-pointer',
        )}
      >
        <Checkbox
          id={historyId}
          className="mt-0.5"
          checked={history}
          disabled={disabled || level === 'none' || historyLocked}
          onCheckedChange={(value) =>
            onChange(workflowLevelPermissions(level, value === true), level)
          }
        />
        <span className="min-w-0">
          <span className="block">История изменений</span>
          <span className="block text-xs text-muted-foreground">
            {historyLocked
              ? 'Пакеты изменений и их история — обязательны для уровней правки'
              : level === 'none'
                ? 'Доступна с уровня «Просмотр»'
                : 'История и пакеты изменений схемы'}
          </span>
        </span>
      </label>

      {level !== 'none' ? <ScopedChip workflowTypes={workflowTypes} /> : null}
    </div>
  );
}
