import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, ExternalLink, Info, Loader2, RefreshCw } from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { syncAdminUsers, updateAdminUser } from '@/shared/api/endpoints/admin';
import { listUsers } from '@/shared/api/endpoints/users';
import type { AdminUser, ListEnvelope } from '@/shared/api/types';
import { ROLE_LABELS, isAppRole } from '@/shared/auth/roles';
import { env } from '@/shared/config/env';
import { formatDateTime } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
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

/**
 * Пользователи и роли (ux.md §14.1, redesign.md §6.9) на DataTable:
 * PG-реплика Keycloak, назначение `manager_id` для эскалаций. Список
 * небольшой — фильтры и пагинация клиентские внутри fetcher-адаптера.
 */

function roleBadge(role: string): ReactNode {
  const status = role === 'admin' ? 'danger' : role === 'head_kam' ? 'progress' : 'draft';
  return (
    <StatusBadge key={role} status={status} size="sm">
      {isAppRole(role) ? ROLE_LABELS[role] : role}
    </StatusBadge>
  );
}

export function AdminUsersPage(): ReactNode {
  const queryClient = useQueryClient();
  const [managerTarget, setManagerTarget] = useState<AdminUser | null>(null);
  const [managerId, setManagerId] = useState<string | null>(null);

  // Полный список — для имён руководителей и выбора в модалке.
  const usersQuery = useQuery({
    queryKey: ['admin-users'],
    queryFn: ({ signal }) => listUsers({ signal }),
  });
  const users = useMemo(() => usersQuery.data?.items ?? [], [usersQuery.data]);
  const usersById = useMemo(() => new Map(users.map((u) => [u.id, u])), [users]);

  /** Клиентский адаптер fetcher: /admin/users без серверной пагинации. */
  const fetcher = useMemo(
    () =>
      async (params: RegistryFetchParams): Promise<ListEnvelope<AdminUser>> => {
        const role = (params.filters.role as string | undefined) || undefined;
        const envelope = await listUsers({ role, signal: params.signal });
        const term = params.search?.trim().toLowerCase() ?? '';
        const filtered = term
          ? envelope.items.filter(
              (user) =>
                user.full_name.toLowerCase().includes(term) ||
                user.email.toLowerCase().includes(term) ||
                user.username.toLowerCase().includes(term),
            )
          : envelope.items;
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
      toastSuccess('Синхронизация с Keycloak выполнена');
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
        render: (_v, user) => (
          <span className="flex flex-wrap gap-1">
            {user.roles.filter((r) => isAppRole(r)).map(roleBadge)}
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
      {
        key: 'actions',
        title: '',
        render: (_v, user) => (
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setManagerTarget(user);
              setManagerId(user.manager_id);
            }}
          >
            Назначить руководителя
          </Button>
        ),
      },
    ],
    [usersById],
  );

  return (
    <>
      <PageHeader
        title="Пользователи и роли"
        extra={
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
        }
      />
      <Alert className="mb-4">
        <Info aria-hidden="true" />
        <AlertTitle>Источник истины — Keycloak (realm «{env.keycloakRealm}»)</AlertTitle>
        <AlertDescription>
          Пользователи, пароли и роли настраиваются в консоли Keycloak; CRM хранит только иерархию
          «кто чей руководитель» для эскалаций.
        </AlertDescription>
      </Alert>
      <DataTable<AdminUser>
        screen="admin.users"
        columns={columns}
        rowKey="id"
        fetcher={fetcher}
        searchPlaceholder="Имя, email или логин"
        renderFilters={({ filters, setFilter }) => (
          <FilterSelect
            placeholder="Роль"
            allLabel="Роль: любая"
            className="min-w-[180px]"
            options={Object.entries(ROLE_LABELS).map(([value, label]) => ({ value, label }))}
            value={filters.role as string | undefined}
            onChange={(value) => setFilter('role', value)}
          />
        )}
        emptyIllustration="registry-empty"
        emptyTitle="Пользователей пока нет"
        emptyDescription="Синхронизируйте список из Keycloak"
      />
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
