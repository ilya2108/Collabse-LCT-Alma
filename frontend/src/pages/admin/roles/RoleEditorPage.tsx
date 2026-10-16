import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Info,
  Loader2,
  Lock,
  RotateCcw,
  Save,
  Trash2,
  TriangleAlert,
  Users,
  Workflow,
} from 'lucide-react';
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react';
import { Link, useBlocker, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { noteOwnAccessChange } from '@/app/layout/AccessChangeListener';
import {
  createRole,
  deleteRole,
  getAdminRole,
  listAdminRoles,
  listPermissions,
  resetRole,
  updateRole,
} from '@/shared/api/endpoints/roles';
import { isApiError, type ApiError } from '@/shared/api/errors';
import type {
  AdminRole,
  AdminRoleDetail,
  PermissionGroup,
  RolePatchBody,
} from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { WORKFLOW_TYPE_LABELS } from '@/shared/auth/permissions';
import { cn } from '@/shared/lib/cn';
import { formatDateTime } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { ROLE_CATALOG_QUERY_KEY } from '@/shared/lib/useRoleCatalog';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog';
import { Button } from '@/shared/ui/button';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  SectionCard,
  StatusBadge,
} from '@/shared/ui/data-table';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { Textarea } from '@/shared/ui/textarea';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { PermissionMatrix } from './PermissionMatrix';
import { RoleTypeBadge } from './RoleChips';
import { RoleScopeFields } from './RoleScopeFields';
import {
  ADMIN_ROLES_QUERY_KEY,
  PERMISSIONS_QUERY_KEY,
  ROLE_DESCRIPTION_MAX,
  ROLE_NAME_MAX,
  emptyRoleForm,
  formFromRole,
  formFromTemplate,
  hasErrors,
  indexPermissions,
  isFormDirty,
  orderedPermissions,
  samePermissions,
  sameWorkflowTypes,
  slugifyRoleCode,
  validateRoleForm,
  type RoleFormErrors,
  type RoleFormState,
} from './roleModel';

/**
 * Редактор роли (F2 §5, ux.md §14.8): `/admin/roles/new?from=<code>` и
 * `/admin/roles/:roleId`. Основное (название, код с автотранслитом), область
 * данных (B2B/B2C, «только свои»), права по группам с автоматическими
 * зависимостями и уровнями конструктора, предупреждение о покрытии переходов,
 * пользователи с ролью. Роль admin — только просмотр (Р-F2-9).
 * Изменения применяются сразу у всех пользователей роли (SSE access.changed).
 */

const USERS_PREVIEW = 10;

function roleQueryKey(roleId: string): readonly unknown[] {
  return ['admin-role', roleId];
}

/** Поле ошибки сервера: `code`, `body.code`, `permissions[3]` → ключ формы. */
function fieldOf(field: string | undefined): keyof RoleFormErrors | null {
  if (!field) return null;
  const last =
    field
      .split('.')
      .pop()
      ?.replace(/\[\d+\]$/, '') ?? '';
  switch (last) {
    case 'name':
      return 'name';
    case 'code':
      return 'code';
    case 'description':
      return 'description';
    case 'workflow_types':
      return 'workflowTypes';
    case 'permissions':
      return 'permissions';
    default:
      return null;
  }
}

/** Ошибки 400/422/409 role_exists → ошибки полей; true — разложено по полям. */
function serverFieldErrors(error: ApiError): RoleFormErrors | null {
  const errors: RoleFormErrors = {};
  for (const detail of error.details) {
    const key = fieldOf(detail.field);
    if (key && !errors[key]) errors[key] = detail.message;
  }
  if (error.code === 'role_exists' && !hasErrors(errors)) errors.code = error.message;
  return hasErrors(errors) ? errors : null;
}

/** «Пользователи с ролью»: первые десять и ссылка на полный список. */
function RoleUsersCard({
  role,
  className,
}: {
  role: AdminRoleDetail;
  className?: string;
}): ReactNode {
  return (
    <SectionCard
      title="Пользователи с ролью"
      className={className}
      extra={
        <Link
          to={`/admin/users?role=${encodeURIComponent(role.code)}`}
          className="text-sm text-primary hover:underline"
        >
          Все
        </Link>
      }
    >
      {(role.users ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">
          Роль никому не назначена.{' '}
          <Link to="/admin/users" className="text-primary hover:underline">
            Назначить в «Пользователях»
          </Link>
        </p>
      ) : (
        <ul className="m-0 list-none space-y-1.5 p-0">
          {(role.users ?? []).slice(0, USERS_PREVIEW).map((user) => (
            <li key={user.id} className="flex min-w-0 items-center gap-2 text-sm">
              <Users className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 truncate">
                {user.full_name}{' '}
                <span className="text-xs text-muted-foreground">{user.username}</span>
              </span>
            </li>
          ))}
          {(role.users ?? []).length > USERS_PREVIEW ? (
            <li className="text-xs text-muted-foreground">
              и ещё {(role.users ?? []).length - USERS_PREVIEW}
            </li>
          ) : null}
        </ul>
      )}
    </SectionCard>
  );
}

export function AdminRoleEditorPage(): ReactNode {
  const { roleId } = useParams<'roleId'>();
  // новый маунт на каждую роль: состояние формы не переезжает между ролями
  return <RoleEditorLoader key={roleId ?? 'new'} roleId={roleId ?? null} />;
}

function RoleEditorLoader({ roleId }: { roleId: string | null }): ReactNode {
  const creating = roleId === null;
  const [searchParams] = useSearchParams();
  const fromCode = creating ? searchParams.get('from') : null;

  const permissionsQuery = useQuery({
    queryKey: PERMISSIONS_QUERY_KEY,
    queryFn: ({ signal }) => listPermissions(signal),
    staleTime: 5 * 60_000,
  });
  const roleQuery = useQuery({
    queryKey: roleQueryKey(roleId ?? ''),
    queryFn: ({ signal }) => getAdminRole(roleId ?? '', signal),
    enabled: !creating,
    retry: (count, error) => !(isApiError(error) && error.status === 404) && count < 1,
  });
  const templateQuery = useQuery({
    queryKey: ADMIN_ROLES_QUERY_KEY,
    queryFn: ({ signal }) => listAdminRoles(signal),
    enabled: Boolean(fromCode),
    staleTime: 10_000,
  });

  const error = permissionsQuery.error ?? roleQuery.error ?? templateQuery.error;
  if (!creating && isApiError(roleQuery.error) && roleQuery.error.status === 404) {
    return (
      <EmptyState
        illustration="search-empty"
        title="Роль не найдена"
        description="Её удалили или ссылка устарела"
        actions={
          <Button asChild>
            <Link to="/admin/roles">К списку ролей</Link>
          </Button>
        }
      />
    );
  }
  if (error) {
    return (
      <ErrorState
        error={error}
        title="Не удалось загрузить роль"
        onRetry={() => {
          void permissionsQuery.refetch();
          if (!creating) void roleQuery.refetch();
          if (fromCode) void templateQuery.refetch();
        }}
      />
    );
  }
  const loading =
    permissionsQuery.isLoading ||
    (!creating && roleQuery.isLoading) ||
    (Boolean(fromCode) && templateQuery.isLoading);
  if (loading || !permissionsQuery.data || (!creating && !roleQuery.data)) {
    return <LoadingState rows={8} />;
  }

  const template = fromCode
    ? templateQuery.data?.find((role) => role.code === fromCode)
    : undefined;
  const initial: RoleFormState = roleQuery.data
    ? formFromRole(roleQuery.data)
    : template
      ? formFromTemplate(template)
      : emptyRoleForm();

  return (
    <RoleEditorForm
      role={roleQuery.data ?? null}
      groups={permissionsQuery.data}
      initial={initial}
      templateName={template?.name ?? null}
      reload={async () => (await roleQuery.refetch()).data}
    />
  );
}

interface RoleEditorFormProps {
  /** null — создание новой роли. */
  role: AdminRoleDetail | null;
  groups: PermissionGroup[];
  initial: RoleFormState;
  /** «Создать на основе»: название роли-образца. */
  templateName: string | null;
  reload: () => Promise<AdminRoleDetail | undefined>;
}

function RoleEditorForm({
  role,
  groups,
  initial,
  templateName,
  reload,
}: RoleEditorFormProps): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { roleCodes, reloadProfile } = useAuth();
  const nameId = useId();
  const codeId = useId();
  const descriptionId = useId();

  const creating = role === null;
  const [current, setCurrent] = useState<AdminRoleDetail | null>(role);
  const locked = Boolean(current?.is_locked);
  const [base, setBase] = useState<RoleFormState>(initial);
  const [form, setForm] = useState<RoleFormState>(initial);
  const [codeTouched, setCodeTouched] = useState(false);
  const [touched, setTouched] = useState<Partial<Record<keyof RoleFormErrors, boolean>>>({});
  const [submitted, setSubmitted] = useState(false);
  const [serverErrors, setServerErrors] = useState<RoleFormErrors>({});
  const [confirmReset, setConfirmReset] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  /** Уход со страницы разрешён (после создания/удаления) — без вопроса о несохранённом. */
  const allowLeaveRef = useRef(false);

  const index = useMemo(() => indexPermissions(groups), [groups]);
  const totalPermissions = index.size;
  const dirty = !locked && isFormDirty(form, base);
  const clientErrors = validateRoleForm(form, { creating });
  const visibleError = (key: keyof RoleFormErrors): string | undefined =>
    serverErrors[key] ?? (submitted || touched[key] ? clientErrors[key] : undefined);

  const patchForm = (patch: Partial<RoleFormState>): void => {
    setForm((prev) => ({ ...prev, ...patch }));
    setServerErrors((prev) => {
      const next = { ...prev };
      for (const key of Object.keys(patch) as Array<keyof RoleFormState>) {
        const errorKey: keyof RoleFormErrors | null =
          key === 'workflowTypes'
            ? 'workflowTypes'
            : key === 'recordScope'
              ? null
              : (key as keyof RoleFormErrors);
        if (errorKey) delete next[errorKey];
      }
      return next;
    });
  };

  // Роль изменили в другом окне (SSE access.changed → свежая карточка): без своих
  // правок просто показываем актуальную версию, со своими — сервер ответит stale_version.
  useEffect(() => {
    if (role && current && role.version !== current.version && !dirty) applySaved(role);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- реагируем только на новую версию с сервера
  }, [role]);

  // --- Несохранённые изменения: уход внутри SPA и закрытие вкладки --------------
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && !allowLeaveRef.current && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (!dirty) return undefined;
    const handler = (event: BeforeUnloadEvent): void => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);

  // --- Сохранение ------------------------------------------------------------------
  const affectsMe = (code: string | undefined): boolean =>
    Boolean(code && roleCodes.includes(code));

  const afterChange = (saved: AdminRoleDetail | null, roleCode: string | undefined): void => {
    noteOwnAccessChange();
    void queryClient.invalidateQueries({ queryKey: [...ADMIN_ROLES_QUERY_KEY] });
    void queryClient.invalidateQueries({ queryKey: ['registry', 'admin.roles'] });
    void queryClient.invalidateQueries({ queryKey: [...ROLE_CATALOG_QUERY_KEY] });
    if (saved) queryClient.setQueryData(roleQueryKey(saved.id), saved);
    // свои права перечитываем сразу, не дожидаясь SSE
    if (affectsMe(roleCode)) void reloadProfile();
  };

  const applySaved = (saved: AdminRoleDetail): void => {
    const next = formFromRole(saved);
    setCurrent(saved);
    setBase(next);
    setForm(next);
    setServerErrors({});
    setSubmitted(false);
    setTouched({});
  };

  const handleError = async (error: unknown, title: string): Promise<void> => {
    if (isApiError(error)) {
      if (error.status === 409 && error.code === 'stale_version') {
        toastError(error, {
          title: 'Роль уже изменил другой администратор — загружена актуальная версия',
        });
        const fresh = await reload();
        if (fresh) applySaved(fresh);
        return;
      }
      if (error.status === 400 || error.status === 422 || error.code === 'role_exists') {
        const fieldErrors = serverFieldErrors(error);
        if (fieldErrors) {
          setServerErrors(fieldErrors);
          setSubmitted(true);
          toastError(error, { title: 'Проверьте поля роли' });
          return;
        }
      }
    }
    // access_lockout, role_locked, role_in_use и прочее — текст сервера и details
    toastError(error, { title });
  };

  /**
   * Мутации отдают роль без пользователей и покрытия переходов — перечитываем
   * карточку; не вышло (сеть) — берём прежние пользователей и покрытие.
   */
  const withDetail = async (saved: AdminRole): Promise<AdminRoleDetail> => {
    try {
      return await getAdminRole(saved.id);
    } catch {
      return {
        ...saved,
        users: current?.users ?? [],
        coverage: current?.coverage ?? {},
        differs_from_default: current?.differs_from_default,
      };
    }
  };

  const saveMutation = useMutation({
    mutationFn: async (): Promise<AdminRoleDetail> => {
      const permissions = orderedPermissions(form.permissions, groups);
      if (!current) {
        const created = await createRole({
          code: form.code,
          name: form.name.trim(),
          description: form.description.trim() || null,
          permissions,
          workflow_types: form.workflowTypes,
          record_scope: form.recordScope,
        });
        return withDetail(created);
      }
      const patch: RolePatchBody = { version: current.version };
      if (form.name.trim() !== base.name.trim()) patch.name = form.name.trim();
      if (form.description.trim() !== base.description.trim()) {
        patch.description = form.description.trim() || null;
      }
      if (!samePermissions(form.permissions, base.permissions)) patch.permissions = permissions;
      if (!sameWorkflowTypes(form.workflowTypes, base.workflowTypes)) {
        patch.workflow_types = form.workflowTypes;
      }
      if (form.recordScope !== base.recordScope) patch.record_scope = form.recordScope;
      return withDetail(await updateRole(current.id, patch));
    },
    meta: { silent: true },
    onSuccess: (saved) => {
      afterChange(saved, saved.code);
      if (!current) {
        toastSuccess(
          `Роль «${saved.name}» создана`,
          'Назначьте её пользователям в разделе «Пользователи»',
        );
        allowLeaveRef.current = true;
        navigate(`/admin/roles/${saved.id}`, { replace: true });
        window.scrollTo({ top: 0 });
        return;
      }
      applySaved(saved);
      toastSuccess(
        'Изменения сохранены',
        saved.users_count > 0
          ? `Применены сразу у ${saved.users_count} польз. — перезаходить не нужно`
          : 'Роль пока никому не назначена',
      );
    },
    onError: (error) => void handleError(error, 'Не удалось сохранить роль'),
  });

  const resetMutation = useMutation({
    mutationFn: async () => withDetail(await resetRole(current?.id ?? '', current?.version ?? 0)),
    meta: { silent: true },
    onSuccess: (saved) => {
      afterChange(saved, saved.code);
      applySaved(saved);
      setConfirmReset(false);
      toastSuccess('Права роли сброшены к стандартным');
    },
    onError: (error) => {
      setConfirmReset(false);
      void handleError(error, 'Не удалось сбросить права роли');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteRole(current?.id ?? ''),
    meta: { silent: true },
    onSuccess: () => {
      afterChange(null, current?.code);
      if (current) queryClient.removeQueries({ queryKey: roleQueryKey(current.id) });
      toastSuccess(`Роль «${current?.name ?? ''}» удалена`);
      setConfirmDelete(false);
      allowLeaveRef.current = true;
      navigate('/admin/roles', { replace: true });
    },
    onError: (error) => {
      setConfirmDelete(false);
      void handleError(error, 'Не удалось удалить роль');
    },
  });

  const busy = saveMutation.isPending || resetMutation.isPending || deleteMutation.isPending;

  const submit = (): void => {
    setSubmitted(true);
    if (hasErrors(clientErrors)) return;
    saveMutation.mutate();
  };

  // --- Предупреждения о покрытии переходов (по сохранённой роли) -------------------
  const coverageWarnings = useMemo(() => {
    if (!current || !current.permissions.includes('requests:transition')) return [];
    return current.workflow_types.flatMap((wf) => {
      const coverage = current.coverage?.[wf];
      if (!coverage || coverage.restricted === 0 || coverage.allowed >= coverage.restricted)
        return [];
      return [{ wf, ...coverage }];
    });
  }, [current]);
  const newRoleNeedsTransitions = creating && form.permissions.has('requests:transition');

  const selectedCount = [...form.permissions].filter((code) => index.has(code)).length;
  const title = current ? current.name : 'Новая роль';

  return (
    <>
      <PageHeader
        title={title}
        backTo="/admin/roles"
        subtitle={
          current
            ? `Код ${current.code} · изменена ${formatDateTime(current.updated_at)}`
            : templateName
              ? `На основе роли «${templateName}» — права и область скопированы`
              : 'Права и область данных новой роли'
        }
        meta={
          current ? (
            <>
              <RoleTypeBadge isSystem={current.is_system} isLocked={current.is_locked} />
              {current.is_system && current.differs_from_default ? (
                <StatusBadge status="warning" size="sm">
                  Права изменены относительно стандартных
                </StatusBadge>
              ) : null}
              <StatusBadge status="draft" size="sm">
                Пользователей: {current.users_count}
              </StatusBadge>
            </>
          ) : undefined
        }
      />

      {locked ? (
        <Alert variant="info" className="mb-4">
          <Lock aria-hidden="true" />
          <AlertTitle className="line-clamp-none">
            Права администратора не редактируются — это защищает систему от потери управления
          </AlertTitle>
          <AlertDescription>
            Роль admin получает все права каталога. Снять её с последнего администратора нельзя.
          </AlertDescription>
        </Alert>
      ) : null}

      {coverageWarnings.map((warning) => (
        <Alert key={warning.wf} variant="warning" className="mb-4">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle className="line-clamp-none">
            В процессе {WORKFLOW_TYPE_LABELS[warning.wf]} роль указана в {warning.allowed} из{' '}
            {warning.restricted} переходов с ограничением по ролям — пользователи не смогут двигать
            заявки по остальным этапам
          </AlertTitle>
          <AlertDescription>
            <Button asChild variant="outline" size="sm" className="mt-1">
              <Link to={`/workflow?wf=${warning.wf}`}>
                <Workflow aria-hidden="true" />
                Открыть конструктор
              </Link>
            </Button>
          </AlertDescription>
        </Alert>
      ))}
      {newRoleNeedsTransitions ? (
        <Alert variant="info" className="mb-4">
          <Info aria-hidden="true" />
          <AlertTitle className="line-clamp-none">
            Новая роль ещё не указана в переходах процессов
          </AlertTitle>
          <AlertDescription>
            После создания добавьте её в переходы с ограничением по ролям в конструкторе процессов —
            иначе пользователи с ролью смогут двигать заявки только по переходам без ограничений.
          </AlertDescription>
        </Alert>
      ) : null}

      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          if (!locked) submit();
        }}
      >
        {/* Телефон — одна колонка в порядке: основное, область, права, пользователи;
            десктоп — слева основное/область/пользователи, справа права */}
        <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:items-start">
          <div className="contents lg:flex lg:min-w-0 lg:flex-col lg:gap-4">
            <SectionCard title="Основное" className="order-1 min-w-0">
              <div className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor={nameId}>Название</Label>
                  <Input
                    id={nameId}
                    value={form.name}
                    maxLength={ROLE_NAME_MAX}
                    disabled={locked || busy}
                    autoComplete="off"
                    placeholder="Например: Аналитик (заказчик)…"
                    aria-invalid={visibleError('name') ? true : undefined}
                    aria-describedby={visibleError('name') ? `${nameId}-error` : undefined}
                    onBlur={() => setTouched((prev) => ({ ...prev, name: true }))}
                    onChange={(event) => {
                      const name = event.target.value;
                      patchForm(
                        creating && !codeTouched ? { name, code: slugifyRoleCode(name) } : { name },
                      );
                    }}
                  />
                  {visibleError('name') ? (
                    <p
                      id={`${nameId}-error`}
                      role="alert"
                      className="text-xs text-status-danger-deep"
                    >
                      {visibleError('name')}
                    </p>
                  ) : null}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={codeId}>Код</Label>
                  {creating ? (
                    <>
                      <Input
                        id={codeId}
                        value={form.code}
                        maxLength={40}
                        disabled={busy}
                        autoComplete="off"
                        spellCheck={false}
                        className="font-mono"
                        placeholder="analitik_zakazchik…"
                        aria-invalid={visibleError('code') ? true : undefined}
                        aria-describedby={`${codeId}-hint`}
                        onBlur={() => setTouched((prev) => ({ ...prev, code: true }))}
                        onChange={(event) => {
                          setCodeTouched(true);
                          patchForm({
                            code: event.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_'),
                          });
                        }}
                      />
                      {visibleError('code') ? (
                        <p role="alert" className="text-xs text-status-danger-deep">
                          {visibleError('code')}
                        </p>
                      ) : null}
                      <p id={`${codeId}-hint`} className="text-xs text-muted-foreground">
                        Латиница строчными, цифры и «_», начинается с буквы. Код попадает в переходы
                        процессов и после создания не меняется.
                      </p>
                    </>
                  ) : (
                    <p id={codeId} className="break-all font-mono text-sm">
                      {current?.code}
                    </p>
                  )}
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor={descriptionId}>Описание</Label>
                  <Textarea
                    id={descriptionId}
                    value={form.description}
                    maxLength={ROLE_DESCRIPTION_MAX}
                    rows={3}
                    disabled={locked || busy}
                    placeholder="Кому и зачем нужна роль…"
                    aria-invalid={visibleError('description') ? true : undefined}
                    onChange={(event) => patchForm({ description: event.target.value })}
                  />
                  {visibleError('description') ? (
                    <p role="alert" className="text-xs text-status-danger-deep">
                      {visibleError('description')}
                    </p>
                  ) : null}
                </div>
              </div>
            </SectionCard>

            <SectionCard title="Область данных" className="order-2 min-w-0">
              <RoleScopeFields
                workflowTypes={form.workflowTypes}
                recordScope={form.recordScope}
                disabled={locked || busy}
                error={visibleError('workflowTypes')}
                onChange={(patch) => patchForm(patch)}
              />
            </SectionCard>

            {current ? <RoleUsersCard role={current} className="order-4 min-w-0" /> : null}
          </div>

          <section
            aria-labelledby="role-permissions-title"
            className="order-3 min-w-0 lg:order-none"
          >
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="role-permissions-title" className="text-base font-semibold">
                Права
              </h2>
              <span className="tabular text-sm text-muted-foreground">
                выбрано {selectedCount} из {totalPermissions}
              </span>
            </div>
            <PermissionMatrix
              // после сохранения — свежий маунт: пояснения о зависимостях сбрасываются
              key={current?.version ?? 'new'}
              groups={groups}
              index={index}
              selected={form.permissions}
              workflowTypes={form.workflowTypes}
              disabled={locked || busy}
              error={visibleError('permissions')}
              onChange={(permissions) => patchForm({ permissions })}
            />
          </section>
        </div>

        {/* Закреплённая панель действий: на телефоне «Отмена» и «Сохранить» — на всю
            ширину в одну строку, сброс или удаление роли — строкой ниже */}
        <div className="sticky bottom-0 z-10 mt-6 rounded-lg border bg-card p-3 shadow-overlay">
          {dirty ? (
            <p
              className="mb-2 text-center text-xs text-status-warning-deep sm:hidden"
              aria-live="polite"
            >
              Есть несохранённые изменения
            </p>
          ) : null}
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="order-2 flex sm:order-1">
              {current && current.is_system && !locked ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="w-full sm:w-auto"
                  disabled={busy}
                  onClick={() => setConfirmReset(true)}
                >
                  <RotateCcw aria-hidden="true" />
                  Сбросить к стандартным правам
                </Button>
              ) : null}
              {current && !current.is_system ? (
                current.users_count > 0 ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex w-full sm:w-auto" tabIndex={0}>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="w-full sm:w-auto"
                          disabled
                        >
                          <Trash2 aria-hidden="true" />
                          Удалить роль
                        </Button>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent className="max-w-xs">
                      Роль назначена {current.users_count} польз. — сначала снимите её в
                      «Пользователях»
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="w-full text-status-danger-deep hover:text-status-danger-deep sm:w-auto"
                    disabled={busy}
                    onClick={() => setConfirmDelete(true)}
                  >
                    <Trash2 aria-hidden="true" />
                    Удалить роль
                  </Button>
                )
              ) : null}
            </div>
            <div
              className={cn(
                'order-1 grid gap-2 sm:order-2 sm:flex sm:items-center',
                locked ? 'grid-cols-1' : 'grid-cols-2',
              )}
            >
              {dirty ? (
                <span
                  className="hidden text-xs text-status-warning-deep sm:mr-2 sm:inline"
                  aria-live="polite"
                >
                  Есть несохранённые изменения
                </span>
              ) : null}
              <Button
                type="button"
                variant="outline"
                className="w-full sm:w-auto"
                disabled={busy}
                onClick={() => navigate('/admin/roles')}
              >
                {locked ? 'К списку ролей' : 'Отмена'}
              </Button>
              {!locked ? (
                <Button
                  type="submit"
                  className="w-full sm:w-auto"
                  disabled={busy || (!creating && !dirty)}
                >
                  {saveMutation.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <Save aria-hidden="true" />
                  )}
                  {creating ? 'Создать роль' : 'Сохранить'}
                </Button>
              ) : null}
            </div>
          </div>
        </div>
      </form>

      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        tone="default"
        title={`Сбросить права роли «${current?.name ?? ''}» к стандартным?`}
        facts={[
          { label: 'права и область — как в поставке системы' },
          { label: `применится сразу у ${current?.users_count ?? 0} польз.` },
        ]}
        confirmLabel="Сбросить"
        loading={resetMutation.isPending}
        onConfirm={() => resetMutation.mutate()}
      />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        tone="danger"
        title={`Удалить роль «${current?.name ?? ''}»?`}
        facts={[
          { label: 'роль исчезнет из каталога и конструктора процессов' },
          { label: 'действие записывается в журнал аудита' },
        ]}
        confirmLabel="Удалить роль"
        loading={deleteMutation.isPending}
        onConfirm={() => deleteMutation.mutate()}
      />

      <AlertDialog open={blocker.state === 'blocked'}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Уйти без сохранения?</AlertDialogTitle>
            <AlertDialogDescription>
              Изменения роли не сохранены и будут потеряны.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel onClick={() => blocker.reset?.()}>Остаться</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-status-danger-deep"
              onClick={() => blocker.proceed?.()}
            >
              Уйти без сохранения
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
