import { useQuery } from '@tanstack/react-query';
import { FileText } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getProgram } from '@/shared/api/endpoints/programs';
import { getProduct } from '@/shared/api/endpoints/products';
import { listRequests } from '@/shared/api/endpoints/requests';
import { getUniversity } from '@/shared/api/endpoints/universities';
import { formatDate, formatText } from '@/shared/lib/format';
import { Badge } from '@/shared/ui/badge';
import {
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
