import { useId, type ReactNode } from 'react';
import type { RecordScope, WorkflowType } from '@/shared/api/types';
import { WORKFLOW_TYPES, WORKFLOW_TYPE_TITLES } from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { Checkbox } from '@/shared/ui/checkbox';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';

/**
 * Область данных роли (Р-F2-4): процессы B2B/B2C (минимум один) и записи —
 * все заявки процесса или только свои. Права с областью (заявки, комментарии,
 * конструктор, отчёты) действуют только в выбранных процессах.
 */

const RECORD_OPTIONS: Array<{ value: RecordScope; label: string; hint: string }> = [
  {
    value: 'all',
    label: 'Все заявки процесса',
    hint: 'Пользователь видит заявки всех ответственных',
  },
  {
    value: 'own',
    label: 'Только заявки, где пользователь — ответственный',
    hint: 'Чужие заявки не видны ни на доске, ни в отчётах',
  },
];

interface RoleScopeFieldsProps {
  workflowTypes: WorkflowType[];
  recordScope: RecordScope;
  onChange: (patch: { workflowTypes?: WorkflowType[]; recordScope?: RecordScope }) => void;
  disabled?: boolean;
  error?: string;
}

export function RoleScopeFields({
  workflowTypes,
  recordScope,
  onChange,
  disabled = false,
  error,
}: RoleScopeFieldsProps): ReactNode {
  const groupId = useId();
  const errorId = `${groupId}-error`;
  return (
    <div className="space-y-4">
      <fieldset
        className="space-y-2"
        aria-describedby={error ? errorId : undefined}
        aria-invalid={error ? true : undefined}
      >
        <legend className="mb-1.5 text-sm font-medium">Процессы</legend>
        <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:gap-4">
          {WORKFLOW_TYPES.map((wf) => {
            const checked = workflowTypes.includes(wf);
            return (
              <label
                key={wf}
                className={cn(
                  'flex min-h-9 items-center gap-2 text-sm',
                  disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer',
                )}
              >
                <Checkbox
                  checked={checked}
                  disabled={disabled}
                  aria-invalid={error ? true : undefined}
                  onCheckedChange={(value) =>
                    onChange({
                      workflowTypes:
                        value === true
                          ? WORKFLOW_TYPES.filter(
                              (item) => item === wf || workflowTypes.includes(item),
                            )
                          : workflowTypes.filter((item) => item !== wf),
                    })
                  }
                />
                {WORKFLOW_TYPE_TITLES[wf]}
              </label>
            );
          })}
        </div>
        {error ? (
          <p id={errorId} role="alert" className="text-xs text-status-danger-deep">
            {error}
          </p>
        ) : null}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="mb-1.5 text-sm font-medium">Заявки</legend>
        <RadioGroup
          value={recordScope}
          disabled={disabled}
          onValueChange={(value) => onChange({ recordScope: value as RecordScope })}
          className="gap-2"
        >
          {RECORD_OPTIONS.map((option) => (
            <label
              key={option.value}
              className={cn(
                'flex items-start gap-2 rounded-md border p-2.5 text-sm transition-colors duration-150',
                recordScope === option.value ? 'border-primary bg-primary-tint' : 'border-border',
                disabled ? 'cursor-not-allowed opacity-70' : 'cursor-pointer hover:bg-accent',
              )}
            >
              <RadioGroupItem value={option.value} className="mt-0.5" />
              <span className="min-w-0">
                <span className="block">{option.label}</span>
                <span className="block text-xs text-muted-foreground">{option.hint}</span>
              </span>
            </label>
          ))}
        </RadioGroup>
      </fieldset>

      <p className="text-xs text-muted-foreground">
        Влияет на доску, реестр заявок, карточку, отчёты, экспорт и поиск.
      </p>
    </div>
  );
}
