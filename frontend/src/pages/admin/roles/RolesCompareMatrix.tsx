import { Check, Minus } from 'lucide-react';
import { Fragment, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { AdminRole, PermissionGroup } from '@/shared/api/types';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { EmptyState } from '@/shared/ui/data-table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/shared/ui/select';
import { RecordScopeLabel, RoleTypeBadge, WorkflowChips } from './RoleChips';

/**
 * Матрица прав «права × роли» (F2 §5): строки — права по группам с заголовками
 * групп, столбцы — роли, первый столбец закреплён; в ячейке ✓ или «—», у прав
 * с областью — процессы роли. На мобильном — выбор роли и список прав с галочками.
 */

interface RolesCompareMatrixProps {
  roles: AdminRole[];
  groups: PermissionGroup[];
}

function Mark({ on, scopedLabel }: { on: boolean; scopedLabel?: string | null }): ReactNode {
  if (!on) {
    return (
      <span className="inline-flex text-muted-foreground" aria-label="нет">
        <Minus className="size-4" aria-hidden="true" />
      </span>
    );
  }
  return (
    <span className="inline-flex flex-col items-center gap-0.5" aria-label="есть">
      <Check className="size-4 text-primary" aria-hidden="true" />
      {scopedLabel ? (
        <span className="whitespace-nowrap text-[10px] leading-3 text-muted-foreground">
          {scopedLabel}
        </span>
      ) : null}
    </span>
  );
}

function scopedLabelFor(role: AdminRole): string {
  return role.workflow_types.map((wf) => WORKFLOW_TYPE_LABELS[wf]).join('·');
}

/** Мобильная версия: роль из списка → права по группам. */
function MobileMatrix({ roles, groups }: RolesCompareMatrixProps): ReactNode {
  const [roleCode, setRoleCode] = useState(roles[0]?.code ?? '');
  const role = roles.find((item) => item.code === roleCode) ?? roles[0];
  if (!role) return null;
  const granted = new Set(role.permissions);
  const scopedLabel = scopedLabelFor(role);

  return (
    <div className="space-y-3">
      <Select value={role.code} onValueChange={setRoleCode}>
        <SelectTrigger className="w-full" aria-label="Роль">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {roles.map((item) => (
            <SelectItem key={item.code} value={item.code}>
              {item.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <RoleTypeBadge isSystem={role.is_system} isLocked={role.is_locked} />
        <WorkflowChips workflowTypes={role.workflow_types} />
        <RecordScopeLabel scope={role.record_scope} />
        <Link to={`/admin/roles/${role.id}`} className="ml-auto text-primary hover:underline">
          Открыть
        </Link>
      </div>
      {groups.map((group) => (
        <section key={group.code} className="rounded-lg border bg-card">
          <h3 className="border-b px-3 py-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {group.name}
          </h3>
          <ul className="m-0 list-none divide-y p-0">
            {group.permissions.map((permission) => {
              const on = granted.has(permission.code);
              return (
                <li key={permission.code} className="flex items-start gap-3 px-3 py-2">
                  <span className="mt-0.5 shrink-0">
                    <Mark on={on} />
                  </span>
                  <span className={cn('min-w-0 text-sm', !on && 'text-muted-foreground')}>
                    {permission.name}
                    {on && permission.scoped ? (
                      <span className="block text-[11px] text-muted-foreground">
                        в процессах: {scopedLabel}
                      </span>
                    ) : null}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function RolesCompareMatrix({ roles, groups }: RolesCompareMatrixProps): ReactNode {
  const { md } = useBreakpoint();
  const grantedByRole = useMemo(
    () => new Map(roles.map((role) => [role.code, new Set(role.permissions)])),
    [roles],
  );

  if (roles.length === 0 || groups.length === 0) {
    return (
      <EmptyState
        illustration="search-empty"
        title="Нечего сравнивать"
        description="Роли или каталог прав пока не загружены"
      />
    );
  }

  if (!md) return <MobileMatrix roles={roles} groups={groups} />;

  return (
    <div className="rounded-lg border bg-card shadow-card">
      {/* Прокрутка только внутри матрицы: страница по горизонтали не едет */}
      <div className="max-h-[70vh] overflow-auto">
        <table className="w-full border-separate border-spacing-0 text-sm">
          <caption className="sr-only">Права ролей: строки — права, столбцы — роли</caption>
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky left-0 top-0 z-30 min-w-[260px] border-b border-r bg-card px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
              >
                Право
              </th>
              {roles.map((role) => (
                <th
                  key={role.code}
                  scope="col"
                  className="sticky top-0 z-20 min-w-[120px] border-b bg-card px-2 py-2 text-center align-bottom font-medium"
                >
                  <Link
                    to={`/admin/roles/${role.id}`}
                    className="block text-sm text-foreground hover:text-primary hover:underline"
                  >
                    {role.name}
                  </Link>
                  <span className="mt-1 flex justify-center">
                    <WorkflowChips workflowTypes={role.workflow_types} />
                  </span>
                  {role.record_scope === 'own' ? (
                    <span className="mt-0.5 block text-[11px] font-normal text-status-warning-deep">
                      только свои
                    </span>
                  ) : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map((group) => (
              <Fragment key={group.code}>
                <tr>
                  <th
                    scope="colgroup"
                    colSpan={roles.length + 1}
                    className="sticky left-0 border-b bg-muted px-3 py-1.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    {group.name}
                  </th>
                </tr>
                {group.permissions.map((permission) => (
                  <tr key={permission.code} className="group">
                    <th
                      scope="row"
                      className="sticky left-0 z-10 border-b border-r bg-card px-3 py-2 text-left font-normal group-hover:bg-accent"
                    >
                      <span className="block">{permission.name}</span>
                      <span className="block font-mono text-[11px] text-muted-foreground">
                        {permission.code}
                        {permission.scoped ? ' · с областью' : ''}
                      </span>
                    </th>
                    {roles.map((role) => {
                      const on = grantedByRole.get(role.code)?.has(permission.code) ?? false;
                      return (
                        <td
                          key={role.code}
                          className="border-b px-2 py-2 text-center group-hover:bg-accent"
                        >
                          <Mark
                            on={on}
                            scopedLabel={on && permission.scoped ? scopedLabelFor(role) : null}
                          />
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
