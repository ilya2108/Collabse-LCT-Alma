import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { listAllItDirections } from '@/shared/api/endpoints/itDirections';
import { getProgram, updateProgram } from '@/shared/api/endpoints/programs';
import { getProduct } from '@/shared/api/endpoints/products';
import { listRequests } from '@/shared/api/endpoints/requests';
import { getUniversity } from '@/shared/api/endpoints/universities';
import type { Program } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDate, formatText } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import {
  Combobox,
  DL,
  DLRow,
  ErrorState,
  LoadingState,
  MiniCard,
  PageHeader,
  SectionCard,
  StageTag,
  StaggerGrid,
  StaggerItem,
  StatusBadge,
} from '@/shared/ui/data-table';

/**
 * ИТ-направление программы: своё (одно) или, если не задано, направления её ИТ-продукта —
 * так же программу и её заявки относят к направлению фильтры отчётов. С правом
 * «Создание и правка программ» направление выбирается прямо в карточке.
 */
function ProgramDirection({ program }: { program: Program }): ReactNode {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = hasPermission('programs:write');
  const directionsQuery = useQuery({
    queryKey: ['it-directions', 'all'],
    queryFn: ({ signal }) => listAllItDirections(signal),
    enabled: canEdit,
    staleTime: 5 * 60_000,
  });
  const mutation = useMutation({
    mutationFn: (directionId: string | null) =>
      updateProgram(program.id, { direction_id: directionId, version: program.version }),
    meta: { silent: true },
    onSuccess: (updated) => {
      queryClient.setQueryData(['program', program.id], updated);
      void queryClient.invalidateQueries({ queryKey: ['programs-ranked'] });
      void queryClient.invalidateQueries({ queryKey: ['it-directions'] });
      toastSuccess(
        updated.direction
          ? `Направление программы: ${updated.direction.name}`
          : 'Направление снято — программа относится к направлениям продукта',
      );
    },
    onError: (error) => toastError(error, { title: 'Не удалось изменить направление' }),
  });
  const inherited = program.product_directions ?? [];
  const fallback =
    inherited.length > 0 ? (
      <span className="text-sm text-muted-foreground">
        по продукту: {inherited.map((d) => d.name).join(', ')}
      </span>
    ) : (
      '—'
    );
  if (!canEdit) {
    return program.direction ? (
      <Badge variant="secondary">{program.direction.name}</Badge>
    ) : (
      fallback
    );
  }
  const options = (directionsQuery.data?.items ?? [])
    .filter((d) => d.is_active || d.id === program.direction_id)
    .map((d) => ({ value: d.id, label: d.is_active ? d.name : `${d.name} (неактивно)` }));
  if (program.direction && !options.some((o) => o.value === program.direction?.id)) {
    options.push({ value: program.direction.id, label: program.direction.name });
  }
  return (
    <span className="flex flex-col gap-1">
      <Combobox
        className="w-full max-w-[280px]"
        options={options}
        value={program.direction_id ?? null}
        onChange={(value) => mutation.mutate(value ?? null)}
        placeholder="Не задано — по продукту"
        searchPlaceholder="Найти направление…"
        emptyText="Направления не найдены"
        loading={directionsQuery.isLoading}
        disabled={mutation.isPending}
        allowClear
        ariaLabel="ИТ-направление программы"
      />
      {!program.direction_id ? fallback : null}
    </span>
  );
}

/** Карточка программы (§6.5): параметры DL-сеткой, приоритет, связи, публикация. */
export function ProgramDetailPage(): ReactNode {
  const { id = '' } = useParams<'id'>();

  const programQuery = useQuery({
    queryKey: ['program', id],
    queryFn: ({ signal }) => getProgram(id, signal),
    enabled: Boolean(id),
  });
  const program = programQuery.data;

  const productQuery = useQuery({
    queryKey: ['product', program?.product_id],
    queryFn: ({ signal }) => getProduct(program?.product_id as string, signal),
    enabled: Boolean(program?.product_id),
  });
  const universityQuery = useQuery({
    queryKey: ['university', program?.university_id],
    queryFn: ({ signal }) => getUniversity(program?.university_id as string, signal),
    enabled: Boolean(program?.university_id),
  });
  const requestsQuery = useQuery({
    queryKey: ['program-requests', id],
    queryFn: ({ signal }) => listRequests({ program_id: id }, { limit: 50, signal }),
    enabled: Boolean(id),
  });

  if (programQuery.isLoading) return <LoadingState rows={8} />;
  if (programQuery.isError || !program) {
    return (
      <ErrorState
        error={programQuery.error}
        title="Не удалось загрузить программу"
        onRetry={() => void programQuery.refetch()}
      />
    );
  }
  const requests = requestsQuery.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={program.name}
        backTo="/registry/programs"
        meta={
          <>
            <StatusBadge status="progress">приоритет {program.priority_rank}</StatusBadge>
            {program.is_active ? (
              <StatusBadge status="success">Активна</StatusBadge>
            ) : (
              <StatusBadge status="draft">Неактивна</StatusBadge>
            )}
          </>
        }
      />
      <StaggerGrid className="grid grid-cols-12 gap-4">
        <div className="col-span-12 lg:col-span-6">
          <StaggerItem>
            <SectionCard title="Параметры">
              <DL labelWidth={170}>
                <DLRow label="Продукт">
                  {program.product_id ? (
                    <Link
                      to={`/registry/products/${program.product_id}`}
                      className="text-primary hover:underline"
                    >
                      {productQuery.data?.name ?? '…'}
                    </Link>
                  ) : (
                    '—'
                  )}
                </DLRow>
                <DLRow label="ИТ-направление">
                  <ProgramDirection program={program} />
                </DLRow>
                <DLRow label="Вуз">
                  {program.university_id ? (
                    <Link
                      to={`/registry/universities/${program.university_id}`}
                      className="text-primary hover:underline"
                    >
                      {universityQuery.data?.name ?? '…'}
                    </Link>
                  ) : (
                    '—'
                  )}
                </DLRow>
                <DLRow label="Старт">{formatDate(program.starts_on)}</DLRow>
                <DLRow label="Мест">
                  <span className="tabular">{program.seats ?? '—'}</span>
                </DLRow>
                <DLRow label="Описание">{formatText(program.description ?? null)}</DLRow>
                <DLRow label="Публикация в CMS">
                  {program.published_to_cms ? (
                    <span className="flex flex-wrap items-center gap-1.5">
                      <StatusBadge status="progress" size="sm">
                        опубликована
                      </StatusBadge>
                      {program.cms_external_id ? (
                        <Badge variant="secondary">ID на сайте: {program.cms_external_id}</Badge>
                      ) : null}
                    </span>
                  ) : (
                    'не публикуется'
                  )}
                </DLRow>
                <DLRow label="Курс LMS">
                  {program.lms_course_id ? (
                    <Badge variant="secondary">{program.lms_course_id}</Badge>
                  ) : (
                    '—'
                  )}
                </DLRow>
              </DL>
            </SectionCard>
          </StaggerItem>
        </div>
        <div className="col-span-12 lg:col-span-6">
          <StaggerItem>
            <SectionCard
              title={`Заявки по программе${requestsQuery.data ? ` (${requestsQuery.data.total})` : ''}`}
            >
              {requestsQuery.isLoading ? (
                <LoadingState rows={3} card={false} />
              ) : requests.length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">Заявок по программе нет</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {requests.map((request) => (
                    <MiniCard
                      key={request.id}
                      to={`/requests/${request.id}`}
                      icon={FileText}
                      title={request.title}
                      extra={
                        <StageTag name={request.status.name} color={request.status.color} size="sm" />
                      }
                    />
                  ))}
                </div>
              )}
            </SectionCard>
          </StaggerItem>
        </div>
      </StaggerGrid>
    </>
  );
}
