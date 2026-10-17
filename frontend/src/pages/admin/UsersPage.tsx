import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRightLeft,
  Check,
  ExternalLink,
  Info,
  KeyRound,
  Loader2,
  RefreshCw,
  UserCog,
} from 'lucide-react';
import { useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import { syncAdminUsers, updateAdminUser } from '@/shared/api/endpoints/admin';
import { UNIVERSITIES_ASSIGN } from '@/shared/api/endpoints/universities';
import {
  listUsers,
  transferPortfolio,
  type PortfolioTransferReport,
} from '@/shared/api/endpoints/users';
import type { AdminUser, ListEnvelope } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { roleBadgeTone } from '@/shared/auth/roles';
import { env } from '@/shared/config/env';
import { formatDateTime } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { useRoleCatalog } from '@/shared/lib/useRoleCatalog';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
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
 * «Передать портфель…» (уход или отпуск КАМа) — право `requests:assign_any`, вузы —
 * `universities:assign`: открытые заявки и закреплённые вузы — другому КАМу одним
 * действием, с предпросмотром и отчётом о результате.
 */

function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function requestsLabel(n: number): string {
  return `${n} ${pluralRu(n, 'открытая заявка', 'открытые заявки', 'открытых заявок')}`;
}

function universitiesLabel(n: number): string {
  return `${n} ${pluralRu(n, 'вуз', 'вуза', 'вузов')}`;
}

/** Сводка отчёта передачи: заявки по процессам, вузы, пропущенные с причиной. */
function TransferSummary({ report }: { report: PortfolioTransferReport }): ReactNode {
  const byWorkflow = Object.entries(report.requests.by_workflow)
    .map(([wf, count]) => `${WORKFLOW_TYPE_LABELS[wf as 'b2b' | 'b2c'] ?? wf} — ${count}`)
    .join(', ');
  return (
    <div className="grid gap-2 text-sm">
      <p className="m-0">
        <span className="font-medium">{requestsLabel(report.requests.transferred)}</span>
        {byWorkflow ? <span className="text-muted-foreground"> ({byWorkflow})</span> : null}
        {report.universities.included ? (
          <>
            {' · '}
            <span className="font-medium">
              {universitiesLabel(report.universities.transferred)}
            </span>
          </>
        ) : null}
      </p>
      {report.requests.skipped_items.length > 0 ? (
        <div className="rounded-md border border-status-warning/40 bg-status-warning-tint px-3 py-2">
          <p className="m-0 font-medium">
            Не передаются: {report.requests.skipped_items.length}
          </p>
          <ul className="m-0 mt-1 list-disc pl-5 text-xs text-muted-foreground">
            {report.requests.skipped_items.slice(0, 5).map((item) => (
              <li key={item.id} className="break-words">
                «{item.title}» — {item.reason}
              </li>
            ))}
            {report.requests.skipped_items.length > 5 ? (
              <li>и ещё {report.requests.skipped_items.length - 5}</li>
            ) : null}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

function TransferPortfolioDialog({
  from,
  onClose,
}: {
  from: AdminUser | null;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const { hasPermission } = useAuth();
  const canAssignUniversities = hasPermission(UNIVERSITIES_ASSIGN);
  const [toUserId, setToUserId] = useState<string | null>(null);
  const [includeUniversities, setIncludeUniversities] = useState(true);
  const [result, setResult] = useState<PortfolioTransferReport | null>(null);
  const withUniversities = canAssignUniversities && includeUniversities;

  const kamsQuery = useQuery({
    queryKey: ['users-select', 'requests:kam'],
    queryFn: ({ signal }) => listUsers({ permission: 'requests:kam', signal }),
    enabled: Boolean(from),
    staleTime: 60_000,
  });
  const options = (kamsQuery.data?.items ?? [])
    .filter((u) => u.id !== from?.id && u.is_active !== false)
    .map((u) => ({ value: u.id, label: u.full_name }));

  const previewQuery = useQuery({
    queryKey: ['portfolio-transfer-preview', from?.id, toUserId, withUniversities],
    queryFn: ({ signal }) =>
      transferPortfolio(
        {
          from_user_id: from!.id,
          to_user_id: toUserId!,
          include_universities: withUniversities,
          dry_run: true,
        },
        signal,
      ),
    enabled: Boolean(from && toUserId) && result === null,
    staleTime: 0,
    gcTime: 0,
  });
  const preview = previewQuery.data;
  const nothingToTransfer =
    preview !== undefined &&
    preview.requests.transferred === 0 &&
    preview.universities.transferred === 0;

  const mutation = useMutation({
    mutationFn: () =>
      transferPortfolio({
        from_user_id: from!.id,
        to_user_id: toUserId!,
        include_universities: withUniversities,
      }),
    meta: { silent: true },
    onSuccess: (report) => {
      setResult(report);
      toastSuccess(
        'Портфель передан',
        `${report.to_user.full_name ?? ''}: ${requestsLabel(report.requests.transferred)}` +
          (report.universities.included
            ? `, ${universitiesLabel(report.universities.transferred)}`
            : ''),
      );
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      void queryClient.invalidateQueries({ queryKey: ['request'] });
      void queryClient.invalidateQueries({ queryKey: ['registry'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось передать портфель' }),
  });

  const close = (): void => {
    setToUserId(null);
    setIncludeUniversities(true);
    setResult(null);
    mutation.reset();
    onClose();
  };

  return (
    <Dialog open={Boolean(from)} onOpenChange={(open) => (open || mutation.isPending ? undefined : close())}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>{from ? `Передать портфель: ${from.full_name}` : ''}</DialogTitle>
          <DialogDescription>
            Открытые заявки и закреплённые вузы перейдут другому КАМу одним действием.
            История заявок, комментарии, файлы и авторство прошлых действий не меняются.
          </DialogDescription>
        </DialogHeader>

        {result ? (
          <div className="grid gap-3">
            <p className="m-0 text-sm">
              Передано сотруднику <span className="font-medium">{result.to_user.full_name}</span>:
            </p>
            <TransferSummary report={result} />
          </div>
        ) : (
          <div className="grid min-w-0 gap-4">
            <div className="grid gap-1.5">
              <span className="text-sm font-medium">Кому передать</span>
              <Combobox
                className="w-full"
                placeholder="Выберите КАМа"
                searchPlaceholder="Имя КАМа…"
                emptyText="КАМы не найдены"
                loading={kamsQuery.isLoading}
                options={options}
                value={toUserId}
                onChange={(value) => setToUserId(value ?? null)}
                ariaLabel="Кому передать портфель"
              />
            </div>
            {canAssignUniversities ? (
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <Checkbox
                  checked={includeUniversities}
                  onCheckedChange={(checked) => setIncludeUniversities(checked === true)}
                />
                Вместе с закреплёнными вузами (КАМ вуза сменится)
              </label>
            ) : (
              <p className="m-0 text-xs text-muted-foreground">
                Вузы передаёт руководитель с правом назначать КАМов вузов — передаются только
                заявки.
              </p>
            )}
            {toUserId ? (
              previewQuery.isLoading ? (
                <p className="m-0 flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" aria-hidden="true" />
                  Считаем портфель…
                </p>
              ) : preview ? (
                <div className="grid gap-1.5">
                  <span className="text-sm font-medium">Будет передано</span>
                  <TransferSummary report={preview} />
                </div>
              ) : previewQuery.isError ? (
                <p className="m-0 text-sm text-status-danger-deep">
                  Не удалось посчитать портфель — попробуйте выбрать сотрудника ещё раз
                </p>
              ) : null
            ) : null}
          </div>
        )}

        <DialogFooter>
          {result ? (
            <Button onClick={close}>Готово</Button>
          ) : (
            <>
              <Button variant="outline" disabled={mutation.isPending} onClick={close}>
                Отмена
              </Button>
              <Button
                disabled={!preview || nothingToTransfer || mutation.isPending}
                onClick={() => mutation.mutate()}
              >
                {mutation.isPending ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <ArrowRightLeft aria-hidden="true" />
                )}
                Передать
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

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
  const [transferFrom, setTransferFrom] = useState<AdminUser | null>(null);

  const canWriteUsers = hasPermission('admin:users_write');
  const canManageRoles = hasPermission('admin:roles');
  // передача портфеля: переназначение чужих заявок хотя бы в одном процессе
  const canTransfer = hasPermission('requests:assign_any');
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
      ...(canManageRoles || canWriteUsers || canTransfer
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
                  {canTransfer ? (
                    <Button
                      variant="outline"
                      size="sm"
                      title="Передать открытые заявки и вузы другому КАМу"
                      aria-label={`Передать портфель: ${user.full_name}`}
                      onClick={() => setTransferFrom(user)}
                    >
                      <ArrowRightLeft aria-hidden="true" />
                      <span className="hidden xl:inline">Портфель…</span>
                    </Button>
                  ) : null}
                </span>
              ),
            } satisfies DataTableColumn<AdminUser>,
          ]
        : []),
    ],
    [usersById, nameOf, canManageRoles, canWriteUsers, canTransfer],
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
      <TransferPortfolioDialog from={transferFrom} onClose={() => setTransferFrom(null)} />
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
