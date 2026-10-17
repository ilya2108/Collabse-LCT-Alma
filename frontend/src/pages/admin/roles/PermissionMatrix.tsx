import { ChevronDown, Info } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import type { PermissionGroup, WorkflowType } from '@/shared/api/types';
import { WORKFLOW_PERMISSION_CODES } from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { Checkbox } from '@/shared/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { Switch } from '@/shared/ui/switch';
import { ScopedChip } from './RoleChips';
import { addPermissions, quotedNames, removePermissions, type PermissionIndex } from './roleModel';
import { WorkflowLevelControl } from './WorkflowLevelControl';

/**
 * Права роли по группам (F2 §5): карточка группы со счётчиком «выбрано N из M»
 * и переключателем всей группы; строка права — флажок, название, описание
 * и чип «в процессах роли» у прав с областью. Зависимости ставятся
 * автоматически, при снятии права снимаются зависящие — с пояснением в группе.
 * Группа «Конструктор процессов» — уровнями (WorkflowLevelControl).
 * На мобильном группы сворачиваются.
 */

const WORKFLOW_GROUP = 'workflow';

interface PermissionMatrixProps {
  groups: PermissionGroup[];
  index: PermissionIndex;
  selected: ReadonlySet<string>;
  workflowTypes: WorkflowType[];
  onChange: (next: Set<string>) => void;
  disabled?: boolean;
  error?: string;
}

interface Notice {
  group: string;
  text: string;
}

function GroupCard({
  group,
  selectedCount,
  allSelected,
  disabled,
  onToggleAll,
  notice,
  children,
}: {
  group: PermissionGroup;
  selectedCount: number;
  allSelected: boolean;
  disabled: boolean;
  onToggleAll: ((on: boolean) => void) | null;
  notice: string | null;
  children: ReactNode;
}): ReactNode {
  const { md } = useBreakpoint();
  // мобильный — группы свёрнуты, кроме тех, где уже есть права
  const [openMobile, setOpenMobile] = useState(selectedCount > 0);
  const open = md || openMobile;
  const headingId = `perm-group-${group.code}`;
  const heading = (
    <span className="block min-w-0 flex-1">
      <span id={headingId} className="block text-sm font-semibold">
        {group.name}
      </span>
      <span className="tabular block text-xs text-muted-foreground">
        выбрано {selectedCount} из {group.permissions.length}
      </span>
    </span>
  );

  return (
    <section aria-labelledby={headingId} className="rounded-lg border bg-card shadow-card">
      <Collapsible open={open} onOpenChange={setOpenMobile}>
        <div className="flex items-center gap-3 px-4 py-3">
          {md ? (
            <div className="min-w-0 flex-1">{heading}</div>
          ) : (
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="flex min-h-10 min-w-0 flex-1 items-center gap-2 text-left outline-none focus-visible:rounded-sm focus-visible:ring-2 focus-visible:ring-ring"
              >
                {heading}
                <ChevronDown
                  aria-hidden="true"
                  className={cn(
                    'size-4 shrink-0 text-muted-foreground transition-transform duration-150',
                    open && 'rotate-180',
                  )}
                />
              </button>
            </CollapsibleTrigger>
          )}
          {onToggleAll ? (
            <Switch
              checked={allSelected}
              disabled={disabled}
              aria-label={`Все права группы «${group.name}»`}
              onCheckedChange={(value) => onToggleAll(value)}
            />
          ) : null}
        </div>
        <CollapsibleContent>
          <div className="border-t px-4 py-3">
            {notice ? (
              <p
                role="status"
                className="mb-3 flex items-start gap-1.5 rounded-md bg-status-progress-tint px-2.5 py-1.5 text-xs text-status-progress-deep"
              >
                <Info className="mt-px size-3.5 shrink-0" aria-hidden="true" />
                <span>{notice}</span>
              </p>
            ) : null}
            {children}
          </div>
        </CollapsibleContent>
      </Collapsible>
    </section>
  );
}

export function PermissionMatrix({
  groups,
  index,
  selected,
  workflowTypes,
  onChange,
  disabled = false,
  error,
}: PermissionMatrixProps): ReactNode {
  const [notice, setNotice] = useState<Notice | null>(null);

  const apply = (group: string, next: Set<string>, text: string | null): void => {
    setNotice(text ? { group, text } : null);
    onChange(next);
  };

  const toggleOne = (group: string, code: string, on: boolean): void => {
    const name = quotedNames([code], index);
    if (on) {
      const { next, cascade } = addPermissions(selected, [code], index);
      apply(
        group,
        next,
        cascade.length > 0
          ? `Добавлено: ${quotedNames(cascade, index)} — требуется для ${name}`
          : null,
      );
    } else {
      const { next, cascade } = removePermissions(selected, [code], index);
      apply(
        group,
        next,
        cascade.length > 0 ? `Снято: ${quotedNames(cascade, index)} — зависят от ${name}` : null,
      );
    }
  };

  const toggleGroup = (group: PermissionGroup, on: boolean): void => {
    const codes = group.permissions.map((p) => p.code);
    if (on) {
      const { next, cascade } = addPermissions(selected, codes, index);
      apply(
        group.code,
        next,
        cascade.length > 0
          ? `Добавлено: ${quotedNames(cascade, index)} — требуется для прав группы`
          : null,
      );
    } else {
      const { next, cascade } = removePermissions(selected, codes, index);
      apply(
        group.code,
        next,
        cascade.length > 0
          ? `Снято: ${quotedNames(cascade, index)} — зависят от прав группы`
          : null,
      );
    }
  };

  return (
    <div className="space-y-3">
      {error ? (
        <p role="alert" className="text-sm text-status-danger-deep">
          {error}
        </p>
      ) : null}
      {groups.map((group) => {
        const codes = group.permissions.map((p) => p.code);
        const selectedCount = codes.filter((code) => selected.has(code)).length;
        const allSelected = selectedCount === codes.length && codes.length > 0;
        const groupNotice = notice?.group === group.code ? notice.text : null;
        const isWorkflow =
          group.code === WORKFLOW_GROUP &&
          codes.every((code) => WORKFLOW_PERMISSION_CODES.has(code));

        return (
          <GroupCard
            key={group.code}
            group={group}
            selectedCount={selectedCount}
            allSelected={allSelected}
            disabled={disabled}
            // уровни конструктора — без переключателя «всё/ничего»
            onToggleAll={isWorkflow ? null : (on) => toggleGroup(group, on)}
            notice={groupNotice}
          >
            {isWorkflow ? (
              <WorkflowLevelControl
                selected={selected}
                workflowTypes={workflowTypes}
                disabled={disabled}
                onChange={(workflowPermissions) => {
                  const next = new Set(
                    [...selected].filter((code) => !WORKFLOW_PERMISSION_CODES.has(code)),
                  );
                  workflowPermissions.forEach((code) => next.add(code));
                  apply(group.code, next, null);
                }}
              />
            ) : (
              <ul className="m-0 list-none space-y-1 p-0">
                {group.permissions.map((permission) => {
                  const checked = selected.has(permission.code);
                  const inputId = `perm-${permission.code.replace(/[^a-z0-9]/gi, '-')}`;
                  return (
                    <li key={permission.code}>
                      <label
                        htmlFor={inputId}
                        className={cn(
                          'flex items-start gap-3 rounded-md px-2 py-2 transition-colors duration-150',
                          disabled ? 'cursor-not-allowed' : 'cursor-pointer hover:bg-accent',
                        )}
                      >
                        <Checkbox
                          id={inputId}
                          className="mt-0.5"
                          checked={checked}
                          disabled={disabled}
                          onCheckedChange={(value) =>
                            toggleOne(group.code, permission.code, value === true)
                          }
                        />
                        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                          <span className="text-sm">{permission.name}</span>
                          {permission.description ? (
                            <span className="text-xs text-muted-foreground">
                              {permission.description}
                            </span>
                          ) : null}
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="font-mono text-[11px] text-muted-foreground">
                              {permission.code}
                            </span>
                            {permission.scoped ? (
                              <ScopedChip workflowTypes={workflowTypes} />
                            ) : null}
                          </span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </GroupCard>
        );
      })}
    </div>
  );
}
