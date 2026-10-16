import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ExternalLink, Info, KeyRound, Loader2, RefreshCw, UserCog } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { syncAdminUsers, updateAdminUser } from '@/shared/api/endpoints/admin';
import { listUsers } from '@/shared/api/endpoints/users';
import type { AdminUser, ListEnvelope } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { roleBadgeTone } from '@/shared/auth/roles';
import { env } from '@/shared/config/env';
import { formatDateTime } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useRoleCatalog } from '@/shared/lib/useRoleCatalog';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import {
  Combobox,
  DataTable,
  FilterSelect,
  PageHeader,
  StatusBadge,
  type DataTableColumn,
  type RegistryFetchParams,
} from '@/shared/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { UserRolesDialog } from './UserRolesDialog';

/**
 * Пользователи и роли (ux.md §14.1, redesign.md §6.9) на DataTable.
 * Роли назначаются здесь, в Альме (F2, Р-F2-1): Keycloak отвечает только за вход,
 * синхронизация переносит профиль и не трогает роли. Просмотр списка — право
 * `admin:users`, руководители и синхронизация — `admin:users_write`, роли —
 * `admin:roles`. Список небольшой — фильтры и пагинация клиентские внутри fetcher.
 */

/** Значение фильтра «Без ролей» (у сервера такого фильтра нет — фильтруем на клиенте). */
const NO_ROLES = '__none__';

function hasNoRoles(user: AdminUser): boolean {
  return Boolean(user.no_roles) || user.roles.length === 0;
}

export function AdminUsersPage(): ReactNode {
  const queryClient = useQueryClient();
  const { hasPermission } = useAuth();
  const roleCatalog = useRoleCatalog();
  const [searchParams] = useSearchParams();
  const [managerTarget, setManagerTarget] = useState<AdminUser | null>(null);
  const [managerId, setManagerId] = useState<string | null>(null);
  const [rolesTarget, setRolesTarget] = useState<AdminUser | null>(null);

  const canWriteUsers = hasPermission('admin:users_write');
  const canManageRoles = hasPermission('admin:roles');
  // ссылка «Пользователи» со страницы ролей: /admin/users?role=<код>
  const initialRole = searchParams.get('role') ?? undefined;

  // Полный список — для имён руководителей и выбора в модалке.
  const usersQuery = useQuery({
    queryKey: ['admin-users'],
    queryFn: ({ signal }) => listUsers({ signal }),
  });
  const users = useMemo(() => usersQuery.data?.items ?? [], [usersQuery.data]);
  const usersById = useMemo(() => new Map(users.map((u) => [u.id, u])), [users]);
  const withoutRoles = useMemo(() => users.filter(hasNoRoles).length, [users]);

  /** Клиентский адаптер fetcher: /admin/users без серверной пагинации. */
  const fetcher = useMemo(
    () =>
      async (params: RegistryFetchParams): Promise<ListEnvelope<AdminUser>> => {
        const roleFilter = (params.filters.role as string | undefined) || undefined;
        const envelope = await listUsers({
          role: roleFilter && roleFilter !== NO_ROLES ? roleFilter : undefined,
          signal: params.signal,
        });
        const term = params.search?.trim().toLowerCase() ?? '';
        const filtered = envelope.items.filter((user) => {
          if (roleFilter === NO_ROLES && !hasNoRoles(user)) return false;
          if (!term) return true;
          return (
            user.full_name.toLowerCase().includes(term) ||
            user.email.toLowerCase().includes(term) ||
            user.username.toLowerCase().includes(term)
          );
        });
        return {
          items: filtered.slice(params.offset, params.offset + params.limit),
          total: filtered.length,
          limit: params.limit,
          offset: params.offset,
        };
      },
    [],
  );

  const syncMutation = useMutation({
    mutationFn: syncAdminUsers,
    meta: { silent: true },
    onSuccess: () => {
      toastSuccess('Синхронизация с Keycloak выполнена', 'Профили обновлены, роли не изменились');
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.users'] });
    },
    onError: (error) => toastError(error, { title: 'Синхронизация не удалась' }),
  });

  const managerMutation = useMutation({
    mutationFn: ({ userId, manager }: { userId: string; manager: string | null }) =>
      updateAdminUser(userId, manager),
    meta: { silent: true },
    onSuccess: () => {
      toastSuccess('Руководитель назначен');
      setManagerTarget(null);
      void queryClient.invalidateQueries({ queryKey: ['admin-users'] });
      void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.users'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось назначить руководителя' }),
  });

  const { nameOf } = roleCatalog;
  const columns: DataTableColumn<AdminUser>[] = useMemo(
    () => [
      {
        key: 'full_name',
        title: 'Имя',
        alwaysVisible: true,
        render: (_v, user) => (
          <span className="block min-w-0">
            <span className="block truncate font-medium">{user.full_name}</span>
            <span className="block truncate text-xs text-muted-foreground">{user.username}</span>
          </span>
        ),
      },
      { key: 'email', title: 'Email', dataIndex: 'email', responsive: ['md'] },
      {
        key: 'roles',
        title: 'Роли',
        render: (_v, user) =>
          hasNoRoles(user) ? (
            <StatusBadge status="warning" size="sm">
              Нет ролей
            </StatusBadge>
          ) : (
            <span className="flex flex-wrap gap-1">
              {user.roles.map((code, index) => (
                <StatusBadge key={code} status={roleBadgeTone(code)} size="sm">
                  {user.role_names?.[index] ?? nameOf(code)}
                </StatusBadge>
              ))}
            </span>
          ),
      },
      {
        key: 'manager',
        title: 'Руководитель (эскалации)',
        responsive: ['lg'],
        render: (_v, user) =>
          user.manager_id ? (usersById.get(user.manager_id)?.full_name ?? '—') : '—',
      },
      {
        key: 'telegram',
        title: 'Telegram',
        width: 110,
        responsive: ['md'],
        render: (_v, user) =>
          user.telegram_linked ? (
            <StatusBadge status="success" icon={Check} size="sm">
              привязан
            </StatusBadge>
          ) : (
            <StatusBadge status="draft" size="sm">
              нет
            </StatusBadge>
          ),
      },
      {
        key: 'last_login',
        title: 'Последний вход',
        responsive: ['lg'],
        render: (_v, user) => formatDateTime(user.last_login_at),
      },
      ...(canManageRoles || canWriteUsers
        ? [
            {
              key: 'actions',
              title: '',
              render: (_v: unknown, user: AdminUser) => (
                <span className="flex justify-end gap-1.5">
                  {canManageRoles ? (
                    <Button variant="outline" size="sm" onClick={() => setRolesTarget(user)}>
                      <KeyRound aria-hidden="true" />
                      Роли…
                    </Button>
                  ) : null}
                  {canWriteUsers ? (
                    <Button
                      variant="outline"
                      size="sm"
                      title="Назначить руководителя для эскалаций"
                      aria-label={`Назначить руководителя: ${user.full_name}`}
                      onClick={() => {
                        setManagerTarget(user);
                        setManagerId(user.manager_id);
                      }}
                    >
                      <UserCog aria-hidden="true" />
                      <span className="hidden xl:inline">Руководитель…</span>
                    </Button>
                  ) : null}
                </span>
              ),
            } satisfies DataTableColumn<AdminUser>,
          ]
        : []),
    ],
    [usersById, nameOf, canManageRoles, canWriteUsers],
  );

  const roleFilterOptions = useMemo(
    () => [
      ...roleCatalog.roles.map((role) => ({ value: role.code, label: role.name })),
      { value: NO_ROLES, label: 'Без ролей' },
    ],
    [roleCatalog.roles],
  );

  return (
    <>
      <PageHeader
        title="Пользователи и роли"
        subtitle={canWriteUsers ? undefined : 'Режим просмотра: руководителей и роли назначает администратор'}
        extra={
          canWriteUsers ? (
            <>
              <Button
                variant="outline"
                disabled={syncMutation.isPending}
                onClick={() => syncMutation.mutate()}
              >
                {syncMutation.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <RefreshCw aria-hidden="true" />
                )}
                Синхронизировать из Keycloak
              </Button>
              <Button variant="outline" asChild>
                <a
                  href={`${env.keycloakUrl}/admin/${env.keycloakRealm}/console/`}
                  target="_blank"
                  rel="noreferrer"
                >
                  <ExternalLink aria-hidden="true" />
                  Консоль Keycloak
                </a>
              </Button>
            </>
          ) : undefined
        }
      />
      <Alert className="mb-4">
        <Info aria-hidden="true" />
        <AlertTitle className="line-clamp-none">
          Роли назначаются здесь; Keycloak отвечает только за вход
        </AlertTitle>
        <AlertDescription>
          <p>
            Новый пользователь получает роли из Keycloak один раз — при первом входе. Дальше роли
            меняются только в Альме и действуют сразу, без перезахода. Синхронизация с Keycloak
            обновляет ФИО и email, роли не трогает.
          </p>
          {withoutRoles > 0 ? (
            <p className="font-medium text-status-warning-deep">
              Без ролей: {withoutRoles} — такие пользователи не могут войти в CRM.
            </p>
          ) : null}
        </AlertDescription>
      </Alert>
      <DataTable<AdminUser>
        screen="admin.users"
        columns={columns}
        rowKey="id"
        fetcher={fetcher}
        defaultFilters={initialRole ? { role: initialRole } : undefined}
        searchPlaceholder="Имя, email или логин"
        renderFilters={({ filters, setFilter }) => (
          <FilterSelect
            placeholder="Роль"
            allLabel="Роль: любая"
            className="min-w-[180px]"
            options={roleFilterOptions}
            value={filters.role as string | undefined}
            onChange={(value) => setFilter('role', value)}
          />
        )}
        emptyIllustration="registry-empty"
        emptyTitle="Пользователей пока нет"
        emptyDescription="Синхронизируйте список из Keycloak"
      />
      <UserRolesDialog user={rolesTarget} onClose={() => setRolesTarget(null)} />
      <Dialog
        open={Boolean(managerTarget)}
        onOpenChange={(open) => (open ? undefined : setManagerTarget(null))}
      >
        <DialogContent className="sm:max-w-[440px]">
          <DialogHeader>
            <DialogTitle>
              {managerTarget ? `Руководитель для: ${managerTarget.full_name}` : ''}
            </DialogTitle>
            <DialogDescription>
              Руководитель получает эскалацию, когда заявка подчинённого висит дольше 1.5× порога.
            </DialogDescription>
          </DialogHeader>
          <Combobox
            className="w-full"
            placeholder="Без руководителя"
            searchPlaceholder="Имя пользователя…"
            options={users
              .filter((u) => u.id !== managerTarget?.id)
              .map((u) => ({ value: u.id, label: u.full_name }))}
            value={managerId}
            onChange={(value) => setManagerId(value ?? null)}
          />
          <DialogFooter>
            <Button variant="outline" onClick={() => setManagerTarget(null)}>
              Отмена
            </Button>
            <Button
              disabled={managerMutation.isPending}
              onClick={() => {
                if (managerTarget) {
                  managerMutation.mutate({ userId: managerTarget.id, manager: managerId });
                }
              }}
            >
              {managerMutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              Сохранить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
