import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  Building2,
  CalendarPlus,
  ExternalLink,
  RefreshCw,
  UserRound,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useMemo, type ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { UserSelect } from '@/components/selects/EntitySelects';
import { useOnboarding } from '@/features/onboarding';
import { isApiError } from '@/shared/api/errors';
import { assignRequest, getRequest } from '@/shared/api/endpoints/requests';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDateTime } from '@/shared/lib/format';
import { toastSuccess } from '@/shared/lib/toast';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { eventRequestId, useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/ui/tabs';
import { UserAvatar } from '@/shared/ui/UserAvatar';
import { StageChip, StuckBadge } from './badges';
import { LoadingPanel, ErrorPanel, NotFoundPanel } from './StatePanels';
import { RequestComments } from './RequestComments';
import { RequestFiles } from './RequestFiles';
import { RequestSidebar } from './RequestSidebar';
import { RequestTimeline } from './RequestTimeline';
import { TransitionMenu } from './TransitionMenu';

/**
 * Карточка заявки (ux.md §8, redesign.md §6.4): единый компонент для страницы
 * /requests/:id и Sheet'а поверх канбана. Шапка-«паспорт»: строка бейджей +
 * сетка фактов 2×2 с иконками вместо текстовой простыни; табы «Таймлайн /
 * Комментарии / Файлы»; связи-атрибуты-интеграции — правая колонка (на
 * планшете и в Sheet — таб «Сведения»).
 */

interface RequestCardProps {
  requestId: string;
  /** Кнопка «Открыть отдельно» и компактная шапка в режиме Sheet. */
  inDrawer?: boolean;
  onNavigateAway?: () => void;
}

/**
 * Строка факта шапки: иконка + метка + значение (§6.4). Текстовые значения
 * усекаются truncate; для факта с интерактивным контролом (селект
 * ответственного) — truncate={false}, иначе overflow:hidden ячейки срезает
 * правый край контрола на планшете — контрол усекает текст внутри
 * собственного триггера.
 */
function Fact({
  icon: Icon,
  label,
  children,
  truncate = true,
}: {
  icon: LucideIcon;
  label: string;
  children: ReactNode;
  /** false — значение с интерактивным контролом: ячейка без overflow:hidden. */
  truncate?: boolean;
}): ReactNode {
  return (
    <div className="flex min-w-0 items-center gap-2 text-sm">
      <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="shrink-0 text-xs text-muted-foreground">{label}</span>
      <span className={truncate ? 'min-w-0 flex-1 truncate' : 'min-w-0 flex-1'}>{children}</span>
    </div>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  cms: 'с сайта',
  import: 'импорт',
  lms: 'из LMS',
};

export function RequestCard({ requestId, inDrawer = false, onNavigateAway }: RequestCardProps): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { user, hasRole, hasPermission } = useAuth();
  const { lg } = useBreakpoint();
  // Чек-лист онбординга §7.5: открытие карточки заявки (no-op без провайдера).
  const { completeChecklistItem } = useOnboarding();
  useEffect(() => {
    completeChecklistItem('open-request');
  }, [completeChecklistItem]);

  const requestQuery = useQuery({
    queryKey: ['request', requestId],
    queryFn: ({ signal }) => getRequest(requestId, signal),
  });

  useSseInvalidate(
    'request.transitioned',
    [
      ['request', requestId],
      ['request-history', requestId],
      ['request-transitions', requestId],
    ],
    (event) => {
      const id = eventRequestId(event);
      return id === null || id === requestId;
    },
  );

  const assignMutation = useMutation({
    mutationFn: (assigneeId: string) => assignRequest(requestId, assigneeId),
    onSuccess: (updated) => {
      toastSuccess(`Ответственный: ${updated.assignee?.full_name ?? '—'}`);
      void queryClient.invalidateQueries({ queryKey: ['request', requestId] });
      void queryClient.invalidateQueries({ queryKey: ['request-history', requestId] });
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
    },
  });

  const request = requestQuery.data;
  const canEdit = useMemo(() => {
    if (!request) return false;
    if (hasRole('admin', 'head_kam')) return true;
    return hasRole('kam') && request.assignee?.id === user?.id;
  }, [request, hasRole, user]);
  const canTransition = canEdit && hasPermission('requests:transition');
  const canReassign = hasRole('admin', 'head_kam');

  if (requestQuery.isLoading) return <LoadingPanel rows={10} />;
  if (requestQuery.isError || !request) {
    if (isApiError(requestQuery.error) && requestQuery.error.status === 404) {
      return (
        <NotFoundPanel
          title="Заявка не найдена или удалена"
          action={
            <Button onClick={() => (onNavigateAway ?? (() => navigate('/board')))()}>
              К доске
            </Button>
          }
        />
      );
    }
    return (
      <ErrorPanel
        error={requestQuery.error}
        title="Не удалось загрузить заявку"
        onRetry={() => void requestQuery.refetch()}
      />
    );
  }

  const clientLabel =
    request.workflow_type === 'b2b'
      ? (request.university?.name ?? null)
      : (request.client?.company_name ?? request.client?.full_name ?? null);

  const header = (
    <div className="min-w-0 flex-1">
      <div className="flex flex-wrap items-center gap-2">
        {!inDrawer ? (
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0"
            aria-label="Назад к доске"
            onClick={() => navigate('/board')}
          >
            <ArrowLeft aria-hidden="true" />
          </Button>
        ) : null}
        {/* в Sheet h1 остаётся за страницей-подложкой (один h1 на экран, §8.2) */}
        {inDrawer ? (
          <h2 className="m-0 text-lg font-semibold leading-6 text-balance">{request.title}</h2>
        ) : (
          <h1 className="m-0 text-xl font-semibold leading-7 text-balance">{request.title}</h1>
        )}
        <StageChip name={request.status.name} color={request.status.color} />
        {request.is_stuck ? <StuckBadge days={request.stuck_days ?? 0} /> : null}
        {request.source !== 'manual' ? (
          <Badge variant="outline">{SOURCE_LABELS[request.source] ?? request.source}</Badge>
        ) : null}
      </div>

      {/* сетка фактов 2×2 (§6.4) */}
      <div className="mt-3 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
        <Fact icon={Building2} label="Контрагент">
          {request.workflow_type === 'b2b' && request.university_id ? (
            <Link
              to={`/registry/universities/${request.university_id}`}
              onClick={onNavigateAway}
              className="text-primary hover:underline"
            >
              {clientLabel ?? 'Карточка вуза'}
            </Link>
          ) : (
            clientLabel ?? '—'
          )}
        </Fact>
        <Fact icon={UserRound} label="Ответственный" truncate={false}>
          <span className="flex min-w-0 items-center gap-1.5">
            <UserAvatar fullName={request.assignee?.full_name} size={22} />
            {canReassign ? (
              /* ширина — flex-basis вместо minWidth: на узкой ячейке селект
                 сжимается, имя усекается внутри триггера Combobox */
              <UserSelect
                size="small"
                className="min-w-0 max-w-[240px] flex-1 basis-[170px]"
                placeholder="Ответственный"
                allowClear={false}
                value={request.assignee?.id}
                loading={assignMutation.isPending}
                onChange={(value) => {
                  if (value && value !== request.assignee?.id) assignMutation.mutate(value);
                }}
              />
            ) : (
              <span className="truncate">{request.assignee?.full_name ?? 'Не назначен'}</span>
            )}
            {/* kam может взять свободную заявку на себя (api-contract.md §12.1) */}
            {!request.assignee && !canReassign && hasRole('kam') && user ? (
              <Button
                variant="outline"
                size="sm"
                disabled={assignMutation.isPending}
                onClick={() => assignMutation.mutate(user.id)}
              >
                Взять на себя
              </Button>
            ) : null}
          </span>
        </Fact>
        <Fact icon={CalendarPlus} label="Создана">
          {formatDateTime(request.created_at)}
        </Fact>
        <Fact icon={RefreshCw} label="Обновлена">
          {formatDateTime(request.updated_at)}
        </Fact>
      </div>
    </div>
  );

  const actions = (
    <div className="flex shrink-0 flex-wrap items-center gap-2">
      {canTransition ? <TransitionMenu request={request} /> : null}
      {inDrawer ? (
        <Button
          variant="outline"
          onClick={() => {
            onNavigateAway?.();
            navigate(`/requests/${request.id}`);
          }}
        >
          <ExternalLink aria-hidden="true" />
          Открыть отдельно
        </Button>
      ) : null}
    </div>
  );

  const sidebar = <RequestSidebar request={request} canEdit={canEdit} />;
  const twoColumns = !inDrawer && lg;

  const tabs = (
    <Tabs defaultValue="timeline">
      <TabsList aria-label="Разделы заявки">
        <TabsTrigger value="timeline">Таймлайн</TabsTrigger>
        <TabsTrigger value="comments">Комментарии</TabsTrigger>
        <TabsTrigger value="files">Файлы</TabsTrigger>
        {!twoColumns ? <TabsTrigger value="details">Сведения</TabsTrigger> : null}
      </TabsList>
      <TabsContent value="timeline">
        <RequestTimeline requestId={request.id} />
      </TabsContent>
      <TabsContent value="comments">
        <RequestComments requestId={request.id} />
      </TabsContent>
      <TabsContent value="files">
        <RequestFiles requestId={request.id} canEdit={canEdit} />
      </TabsContent>
      {!twoColumns ? <TabsContent value="details">{sidebar}</TabsContent> : null}
    </Tabs>
  );

  if (inDrawer) {
    return (
      <div>
        <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
          {header}
          {actions}
        </div>
        {tabs}
      </div>
    );
  }

  return (
    <>
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        {header}
        {actions}
      </div>
      {twoColumns ? (
        <div className="grid grid-cols-[minmax(0,1fr)_340px] items-start gap-4">
          <div className="rounded-lg border bg-card p-5 shadow-card">{tabs}</div>
          {sidebar}
        </div>
      ) : (
        <div className="rounded-lg border bg-card p-5 shadow-card">{tabs}</div>
      )}
    </>
  );
}
