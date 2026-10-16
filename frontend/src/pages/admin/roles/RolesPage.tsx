import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Copy, Ellipsis, FolderOpen, Plus, Trash2, Users } from 'lucide-react';
import { useMemo, useState, type ReactNode, type SyntheticEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { noteOwnAccessChange } from '@/app/layout/AccessChangeListener';
import { deleteRole, listAdminRoles, listPermissions } from '@/shared/api/endpoints/roles';
import type { AdminRole, ListEnvelope } from '@/shared/api/types';
import { formatDate } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { ROLE_CATALOG_QUERY_KEY } from '@/shared/lib/useRoleCatalog';
import { Button } from '@/shared/ui/button';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import {
  DataTable,
  ErrorState,
  LoadingState,
  PageHeader,
  type DataTableColumn,
  type RegistryFetchParams,
} from '@/shared/ui/data-table';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/ui/tabs';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { RecordScopeLabel, RoleTypeBadge, WorkflowChips } from './RoleChips';
import { RolesCompareMatrix } from './RolesCompareMatrix';
import {
  ADMIN_ROLES_QUERY_KEY,
  PERMISSIONS_QUERY_KEY,
  PERMISSIONS_TOTAL_FALLBACK,
} from './roleModel';

/**
 * «Роли и права» (F2 §5, ux.md §14.8): список ролей и матрица прав.
 * Системные роли (4 базовые) и свои — «КАМ B2B», «Аналитик (заказчик)» и т. п.
 * Изменения применяются сразу, без перезахода пользователей (SSE access.changed).
 * Доступ — право `admin:roles`.
 */

type RolesTab = 'roles' | 'matrix';

/** Ячейка с меню строки: клик по ней не открывает редактор. */
function stopRowClick(event: SyntheticEvent): void {
  event.stopPropagation();
}

export function AdminRolesPage(): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const tab: RolesTab = searchParams.get('tab') === 'matrix' ? 'matrix' : 'roles';
  const [deleteTarget, setDeleteTarget] = useState<AdminRole | null>(null);

  const rolesQuery = useQuery({
    queryKey: ADMIN_ROLES_QUERY_KEY,
    queryFn: ({ signal }) => listAdminRoles(signal),
    staleTime: 10_000,
  });
  const permissionsQuery = useQuery({
    queryKey: PERMISSIONS_QUERY_KEY,
    queryFn: ({ signal }) => listPermissions(signal),
    staleTime: 5 * 60_000,
  });
  const permissionsTotal = useMemo(
    () =>
      permissionsQuery.data?.reduce((sum, group) => sum + group.permissions.length, 0) ??
      PERMISSIONS_TOTAL_FALLBACK,
    [permissionsQuery.data],
  );

  // изменения ролей другим администратором перечитывает AccessChangeListener (access.changed)

  const invalidateRoles = (): void => {
    void queryClient.invalidateQueries({ queryKey: [...ADMIN_ROLES_QUERY_KEY] });
    void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.roles'] });
    void queryClient.invalidateQueries({ queryKey: [...ROLE_CATALOG_QUERY_KEY] });
  };

  const deleteMutation = useMutation({
    mutationFn: (role: AdminRole) => deleteRole(role.id),
    meta: { silent: true },
    onSuccess: (_data, role) => {
      noteOwnAccessChange();
      toastSuccess(`Роль «${role.name}» удалена`);
      setDeleteTarget(null);
      invalidateRoles();
    },
    onError: (error) => {
      // 409 role_in_use / role_system — текст сервера и список из details
      toastError(error, { title: 'Не удалось удалить роль' });
      setDeleteTarget(null);
      invalidateRoles();
    },
  });

  /** Клиентский адаптер: ролей единицы — поиск и пагинация на клиенте. */
  const fetcher = useMemo(
    () =>
      async (params: RegistryFetchParams): Promise<ListEnvelope<AdminRole>> => {
        const items = await queryClient.fetchQuery({
          queryKey: [...ADMIN_ROLES_QUERY_KEY],
          queryFn: ({ signal }) => listAdminRoles(signal),
          staleTime: 0,
        });
        const term = params.search?.trim().toLowerCase() ?? '';
        const filtered = term
          ? items.filter(
              (role) =>
                role.name.toLowerCase().includes(term) || role.code.toLowerCase().includes(term),
            )
          : items;
        return {
          items: filtered.slice(params.offset, params.offset + params.limit),
          total: filtered.length,
          limit: params.limit,
          offset: params.offset,
        };
      },
    [queryClient],
  );

  const columns: DataTableColumn<AdminRole>[] = useMemo(
    () => [
      {
        key: 'name',
        title: 'Роль',
        alwaysVisible: true,
        render: (_v, role) => (
          <span className="block min-w-0">
            <span className="block truncate font-medium">{role.name}</span>
            <span className="block truncate font-mono text-xs text-muted-foreground">
              {role.code}
            </span>
            {/* на телефоне процессы и область — под названием, без лишних колонок */}
            <span className="mt-1 flex flex-wrap items-center gap-1.5 md:hidden">
              <WorkflowChips workflowTypes={role.workflow_types} />
              {role.record_scope === 'own' ? <RecordScopeLabel scope="own" /> : null}
              <span className="tabular inline-flex items-center gap-1 text-xs text-muted-foreground">
                <Users className="size-3" aria-hidden="true" />
                {role.users_count}
              </span>
            </span>
          </span>
        ),
      },
      {
        key: 'type',
        title: 'Тип',
        responsive: ['md'],
        render: (_v, role) => <RoleTypeBadge isSystem={role.is_system} isLocked={role.is_locked} />,
      },
      {
        key: 'workflow_types',
        title: 'Процессы',
        responsive: ['md'],
        render: (_v, role) => (
          <WorkflowChips workflowTypes={role.workflow_types} className="flex-nowrap" />
        ),
      },
      {
        key: 'record_scope',
        title: 'Записи',
        responsive: ['md'],
        render: (_v, role) => <RecordScopeLabel scope={role.record_scope} />,
      },
      {
        key: 'users_count',
        title: 'Пользователи',
        align: 'right',
        responsive: ['md'],
        render: (_v, role) => (
          <Link
            to={`/admin/users?role=${encodeURIComponent(role.code)}`}
            onClick={stopRowClick}
            className="tabular inline-flex items-center gap-1 text-primary hover:underline"
            aria-label={`Пользователи с ролью «${role.name}»: ${role.users_count}`}
          >
            <Users className="size-3.5" aria-hidden="true" />
            {role.users_count}
          </Link>
        ),
      },
      {
        key: 'permissions_count',
        title: 'Права',
        responsive: ['lg'],
        render: (_v, role) => (
          <span className="tabular whitespace-nowrap">
            {role.permissions_count} из {permissionsTotal}
          </span>
        ),
      },
      {
        key: 'updated_at',
        title: 'Изменена',
        responsive: ['lg'],
        render: (_v, role) => (
          <span className="tabular whitespace-nowrap">{formatDate(role.updated_at)}</span>
        ),
      },
      {
        key: 'actions',
        title: '',
        width: 48,
        render: (_v, role) => (
          <span onClick={stopRowClick} onKeyDown={stopRowClick} className="flex justify-end">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-8"
                  aria-label={`Действия: ${role.name}`}
                >
                  <Ellipsis aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuItem onSelect={() => navigate(`/admin/roles/${role.id}`)}>
                  <FolderOpen aria-hidden="true" />
                  Открыть
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={() =>
                    navigate(`/admin/roles/new?from=${encodeURIComponent(role.code)}`)
                  }
                >
                  <Copy aria-hidden="true" />
                  Создать на основе
                </DropdownMenuItem>
                {!role.is_system ? (
                  <>
                    <DropdownMenuSeparator />
                    {role.users_count > 0 ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          {/* обёртка: у неактивного пункта нет событий мыши для подсказки */}
                          <span className="block">
                            <DropdownMenuItem variant="destructive" disabled>
                              <Trash2 aria-hidden="true" />
                              Удалить
                            </DropdownMenuItem>
                          </span>
                        </TooltipTrigger>
                        <TooltipContent side="left" className="max-w-[220px]">
                          Роль назначена {role.users_count} польз. — сначала снимите её в
                          «Пользователях»
                        </TooltipContent>
                      </Tooltip>
                    ) : (
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setDeleteTarget(role)}
                      >
                        <Trash2 aria-hidden="true" />
                        Удалить
                      </DropdownMenuItem>
                    )}
                  </>
                ) : null}
              </DropdownMenuContent>
            </DropdownMenu>
          </span>
        ),
      },
    ],
    [navigate, permissionsTotal],
  );

  const setTab = (next: RolesTab): void => {
    setSearchParams(
      (prev) => {
        const params = new URLSearchParams(prev);
        if (next === 'matrix') params.set('tab', 'matrix');
        else params.delete('tab');
        return params;
      },
      { replace: true },
    );
  };

  let matrixBody: ReactNode;
  if (rolesQuery.isError || permissionsQuery.isError) {
    matrixBody = (
      <ErrorState
        error={rolesQuery.error ?? permissionsQuery.error}
        title="Не удалось загрузить матрицу прав"
        onRetry={() => {
          void rolesQuery.refetch();
          void permissionsQuery.refetch();
        }}
      />
    );
  } else if (rolesQuery.isLoading || permissionsQuery.isLoading) {
    matrixBody = <LoadingState rows={6} />;
  } else {
    matrixBody = (
      <RolesCompareMatrix roles={rolesQuery.data ?? []} groups={permissionsQuery.data ?? []} />
    );
  }

  return (
    <>
      <PageHeader
        title="Роли и права"
        subtitle="Набор прав и область данных роли. Изменения применяются сразу, без перезахода пользователей"
        extra={
          <Button asChild>
            <Link to="/admin/roles/new">
              <Plus aria-hidden="true" />
              Создать роль
            </Link>
          </Button>
        }
      />
      <Tabs value={tab} onValueChange={(value) => setTab(value as RolesTab)}>
        <TabsList aria-label="Представление">
          <TabsTrigger value="roles">Роли</TabsTrigger>
          <TabsTrigger value="matrix">Матрица прав</TabsTrigger>
        </TabsList>
        <TabsContent value="roles">
          <DataTable<AdminRole>
            screen="admin.roles"
            columns={columns}
            rowKey="id"
            fetcher={fetcher}
            searchPlaceholder="Название или код роли"
            onRowClick={(role) => navigate(`/admin/roles/${role.id}`)}
            emptyIllustration="registry-empty"
            emptyTitle="Ролей пока нет"
            emptyDescription="Системные роли создаёт миграция — проверьте, что она применена"
          />
        </TabsContent>
        <TabsContent value="matrix">{matrixBody}</TabsContent>
      </Tabs>

      <ConfirmDialog
        open={deleteTarget !== null}
        onOpenChange={(open) => (!open ? setDeleteTarget(null) : undefined)}
        tone="danger"
        title={deleteTarget ? `Удалить роль «${deleteTarget.name}»?` : ''}
        facts={[
          { label: 'роль исчезнет из каталога и конструктора процессов' },
          { label: 'действие записывается в журнал аудита' },
        ]}
        confirmLabel="Удалить роль"
        loading={deleteMutation.isPending}
        onConfirm={() => {
          if (deleteTarget) deleteMutation.mutate(deleteTarget);
        }}
      />
    </>
  );
}
