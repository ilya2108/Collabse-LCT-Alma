import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Lock, TriangleAlert } from 'lucide-react';
import { useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { noteOwnAccessChange } from '@/app/layout/AccessChangeListener';
import { listAdminRoles, setUserRoles } from '@/shared/api/endpoints/roles';
import { isApiError } from '@/shared/api/errors';
import type { AdminRole, AdminUser, WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { WORKFLOW_TYPES, WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { roleBadgeTone } from '@/shared/auth/roles';
import { cn } from '@/shared/lib/cn';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
import { StatusBadge } from '@/shared/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Skeleton } from '@/shared/ui/skeleton';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * Назначение ролей пользователю (F2 §5, ux.md §14.1): флажки ролей с описанием,
 * процессами и отметкой «только свои», предпросмотр итогового доступа.
 * Роли назначаются в Альме (`PUT /admin/users/{id}/roles`) и действуют сразу —
 * пользователь получает SSE `access.changed` и перечитывает профиль без перезахода.
 * Снять с себя право управления ролями нельзя (Р-F2-9): такие флажки заблокированы.
 */

const ADMIN_ROLES_PERMISSION = 'admin:roles';

interface AccessPreview {
  workflowTypes: WorkflowType[];
  ownOnly: Set<WorkflowType>;
  permissionsCount: number;
  hasAdminRoles: boolean;
}

/** Итоговый доступ набора ролей: объединение прав, процессы, «только свои» по процессу. */
function previewAccess(roles: AdminRole[]): AccessPreview {
  const permissions = new Set<string>();
  const workflowTypes = new Set<WorkflowType>();
  const allRecords = new Set<WorkflowType>();
  for (const role of roles) {
    role.permissions.forEach((code) => permissions.add(code));
    for (const wf of role.workflow_types) {
      workflowTypes.add(wf);
      if (role.record_scope === 'all') allRecords.add(wf);
    }
  }
  const ordered = WORKFLOW_TYPES.filter((wf) => workflowTypes.has(wf));
  return {
    workflowTypes: ordered,
    // «только свои» в процессе — если ни одна роль процесса не даёт все заявки
    ownOnly: new Set(ordered.filter((wf) => !allRecords.has(wf))),
    permissionsCount: permissions.size,
    hasAdminRoles: permissions.has(ADMIN_ROLES_PERMISSION),
  };
}

function ScopeChips({ role }: { role: AdminRole }): ReactNode {
  return (
    <span className="flex flex-wrap items-center gap-1">
      {role.workflow_types.map((wf) => (
        <span
          key={wf}
          className="rounded-sm bg-muted px-1.5 py-px text-[11px] font-medium text-foreground"
        >
          {WORKFLOW_TYPE_LABELS[wf]}
        </span>
      ))}
      {role.record_scope === 'own' ? (
        <span className="rounded-sm bg-status-warning-tint px-1.5 py-px text-[11px] font-medium text-status-warning-deep">
          только свои
        </span>
      ) : null}
    </span>
  );
}

interface UserRolesDialogProps {
  user: AdminUser | null;
  onClose: () => void;
}

export function UserRolesDialog({ user, onClose }: UserRolesDialogProps): ReactNode {
  const queryClient = useQueryClient();
  const { user: me } = useAuth();
  const titleId = useId();
  const [selected, setSelected] = useState<string[]>([]);

  const rolesQuery = useQuery({
    queryKey: ['admin-roles'],
    queryFn: ({ signal }) => listAdminRoles(signal),
    enabled: Boolean(user),
    staleTime: 10_000,
  });
  const roles = useMemo(() => rolesQuery.data ?? [], [rolesQuery.data]);
  const roleByCode = useMemo(() => new Map(roles.map((role) => [role.code, role])), [roles]);

  useEffect(() => {
    if (user) setSelected(user.roles);
  }, [user]);

  const isSelf = Boolean(user && me && user.id === me.id);
  const selectedRoles = useMemo(
    () => selected.flatMap((code) => roleByCode.get(code) ?? []),
    [selected, roleByCode],
  );
  const preview = useMemo(() => previewAccess(selectedRoles), [selectedRoles]);
  const unchanged =
    user !== null &&
    selected.length === user.roles.length &&
    selected.every((code) => user.roles.includes(code));

  /** Снятие роли лишит себя права управления ролями — флажок заблокирован. */
  const locksSelfOut = (code: string): boolean => {
    if (!isSelf || !selected.includes(code)) return false;
    const rest = selectedRoles.filter((role) => role.code !== code);
    return previewAccess(rest).hasAdminRoles === false && preview.hasAdminRoles;
  };

  const mutation = useMutation({
    mutationFn: () => setUserRoles(user?.id ?? '', selected, user?.version),
    meta: { silent: true },
    onSuccess: (updated) => {
      if (isSelf) noteOwnAccessChange();
      toastSuccess(
        `Роли пользователя ${updated.full_name || user?.full_name || ''} обновлены`,
        'Права применятся сразу — перезаходить не нужно',
      );
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.users'] });
      void queryClient.invalidateQueries({ queryKey: ['admin-roles'] });
      void queryClient.invalidateQueries({ queryKey: ['admin-role'] });
      void queryClient.invalidateQueries({ queryKey: ['users-select'] });
      onClose();
    },
    onError: (error) => {
      if (isApiError(error) && error.code === 'stale_version') {
        toastError(error, { title: 'Пользователя уже изменили — список обновлён' });
        void queryClient.invalidateQueries({ queryKey: ['admin-users'] });
        void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.users'] });
        onClose();
        return;
      }
      toastError(error, { title: 'Не удалось назначить роли' });
    },
  });

  const toggle = (code: string, checked: boolean): void => {
    setSelected((prev) =>
      checked ? [...prev.filter((c) => c !== code), code] : prev.filter((c) => c !== code),
    );
  };

  // роли пользователя, которых уже нет в каталоге (удалены) — показываем, чтобы их можно было снять
  const unknownCodes = selected.filter((code) => !roleByCode.has(code) && roles.length > 0);

  return (
    <Dialog
      open={Boolean(user)}
      onOpenChange={(open) => (!open && !mutation.isPending ? onClose() : undefined)}
    >
      <DialogContent
        aria-labelledby={titleId}
        className="flex max-h-[85vh] flex-col gap-4 overflow-hidden sm:max-w-[560px]"
      >
        <DialogHeader>
          <DialogTitle id={titleId} className="text-balance pr-6">
            {user ? `Роли: ${user.full_name}` : ''}
          </DialogTitle>
          <DialogDescription>
            Права ролей объединяются по процессу. Изменения применяются сразу, без перезахода
            пользователя.
          </DialogDescription>
        </DialogHeader>

        <div className="-mx-6 min-h-0 flex-1 overflow-y-auto px-6">
          {rolesQuery.isLoading ? (
            <div className="space-y-3" aria-hidden="true">
              {Array.from({ length: 4 }, (_, i) => (
                <Skeleton key={i} className="h-14" />
              ))}
            </div>
          ) : rolesQuery.isError ? (
            <Alert variant="destructive">
              <TriangleAlert aria-hidden="true" />
              <AlertTitle>Не удалось загрузить роли</AlertTitle>
              <AlertDescription>
                <Button variant="outline" size="sm" onClick={() => void rolesQuery.refetch()}>
                  Повторить
                </Button>
              </AlertDescription>
            </Alert>
          ) : (
            <ul className="m-0 list-none space-y-2 p-0" aria-label="Роли пользователя">
              {roles.map((role) => {
                const checked = selected.includes(role.code);
                const blocked = locksSelfOut(role.code);
                const row = (
                  <label
                    className={cn(
                      'flex cursor-pointer items-start gap-3 rounded-md border p-3 transition-colors duration-150',
                      checked ? 'border-primary bg-primary-tint' : 'border-border hover:bg-accent',
                      blocked && 'cursor-not-allowed opacity-80',
                    )}
                  >
                    <Checkbox
                      className="mt-0.5"
                      checked={checked}
                      disabled={blocked || mutation.isPending}
                      onCheckedChange={(value) => toggle(role.code, value === true)}
                      aria-describedby={`role-desc-${role.code}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <span className="text-sm font-medium">{role.name}</span>
                        <StatusBadge status={roleBadgeTone(role.code)} size="sm">
                          {role.is_system ? 'Системная' : 'Своя'}
                        </StatusBadge>
                        {blocked ? (
                          <Lock
                            className="size-3.5 text-muted-foreground"
                            aria-label="Заблокировано"
                          />
                        ) : null}
                      </span>
                      <span
                        id={`role-desc-${role.code}`}
                        className="mt-0.5 block text-xs text-muted-foreground"
                      >
                        {role.description || `${role.permissions_count} прав`}
                      </span>
                      <span className="mt-1.5 block">
                        <ScopeChips role={role} />
                      </span>
                    </span>
                  </label>
                );
                return (
                  <li key={role.code}>
                    {blocked ? (
                      <Tooltip>
                        <TooltipTrigger asChild>{row}</TooltipTrigger>
                        <TooltipContent className="max-w-xs">
                          Нельзя снять с себя право управления ролями — попросите другого
                          администратора
                        </TooltipContent>
                      </Tooltip>
                    ) : (
                      row
                    )}
                  </li>
                );
              })}
              {unknownCodes.map((code) => (
                <li key={code}>
                  <label className="flex cursor-pointer items-start gap-3 rounded-md border border-dashed p-3">
                    <Checkbox
                      className="mt-0.5"
                      checked
                      onCheckedChange={(value) => toggle(code, value === true)}
                    />
                    <span className="min-w-0 text-sm">
                      <span className="font-mono text-xs">{code}</span>
                      <span className="block text-xs text-muted-foreground">
                        Роли нет в каталоге — снимите её
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded-md bg-muted px-3 py-2 text-sm" aria-live="polite">
          {selected.length === 0 ? (
            <span className="flex items-start gap-1.5 text-status-warning-deep">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              Без ролей пользователь не сможет войти в CRM
            </span>
          ) : (
            <span>
              Итоговые процессы:{' '}
              <span className="font-medium">
                {preview.workflowTypes.length > 0
                  ? preview.workflowTypes
                      .map(
                        (wf) =>
                          `${WORKFLOW_TYPE_LABELS[wf]}${preview.ownOnly.has(wf) ? ' (только свои)' : ''}`,
                      )
                      .join(', ')
                  : '—'}
              </span>
              {' · '}прав: <span className="tabular font-medium">{preview.permissionsCount}</span>
            </span>
          )}
        </div>

        <DialogFooter className="items-center sm:justify-between">
          <Button variant="link" className="h-auto px-0" asChild>
            <Link to="/admin/roles" onClick={onClose}>
              Настроить роли
            </Link>
          </Button>
          <div className="flex w-full flex-col-reverse gap-2 sm:w-auto sm:flex-row">
            <Button variant="outline" disabled={mutation.isPending} onClick={onClose}>
              Отмена
            </Button>
            <Button
              disabled={unchanged || mutation.isPending || rolesQuery.isLoading}
              onClick={() => mutation.mutate()}
            >
              {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Сохранить
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
