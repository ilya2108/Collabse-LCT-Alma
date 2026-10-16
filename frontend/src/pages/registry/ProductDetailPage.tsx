import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FileText, GraduationCap } from 'lucide-react';
import type { ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { listAllItDirections } from '@/shared/api/endpoints/itDirections';
import { getProduct, updateProduct } from '@/shared/api/endpoints/products';
import { listProgramsByProduct } from '@/shared/api/endpoints/programs';
import { listRequests } from '@/shared/api/endpoints/requests';
import type { Product } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDate, formatText } from '@/shared/lib/format';
import { toastError } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import {
  DL,
  DLRow,
  ErrorState,
  LoadingState,
  MiniCard,
  MultiCombobox,
  PageHeader,
  SectionCard,
  StageTag,
  StaggerGrid,
  StaggerItem,
  StatusBadge,
} from '@/shared/ui/data-table';
import { PRODUCT_TYPE_LABELS } from './ProductsPage';

/**
 * ИТ-направления продукта (M:N): продукт помогает учить по одному или нескольким. С правом
 * «Создание и правка продуктов» набор правится прямо в карточке; программы продукта без
 * своего направления относятся к этим направлениям.
 */
function ProductDirections({ product }: { product: Product }): ReactNode {
  const { hasPermission } = useAuth();
  const queryClient = useQueryClient();
  const canEdit = hasPermission('products:write');
  const directionsQuery = useQuery({
    queryKey: ['it-directions', 'all'],
    queryFn: ({ signal }) => listAllItDirections(signal),
    enabled: canEdit,
    staleTime: 5 * 60_000,
  });
  const mutation = useMutation({
    mutationFn: (directionIds: string[]) =>
      updateProduct(product.id, { direction_ids: directionIds, version: product.version }),
    meta: { silent: true },
    onSuccess: (updated) => {
      queryClient.setQueryData(['product', product.id], updated);
      void queryClient.invalidateQueries({ queryKey: ['registry', 'registry.products'] });
      void queryClient.invalidateQueries({ queryKey: ['product-programs', product.id] });
      void queryClient.invalidateQueries({ queryKey: ['it-directions'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось изменить направления' }),
  });
  const current = product.directions ?? [];
  if (!canEdit) {
    return current.length > 0 ? (
      <span className="flex flex-wrap gap-1">
        {current.map((d) => (
          <Badge key={d.id} variant="secondary">
            {d.name}
          </Badge>
        ))}
      </span>
    ) : (
      '—'
    );
  }
  const selected = new Set(product.direction_ids ?? current.map((d) => d.id));
  const options = (directionsQuery.data?.items ?? [])
    .filter((d) => d.is_active || selected.has(d.id))
    .map((d) => ({ value: d.id, label: d.is_active ? d.name : `${d.name} (неактивно)` }));
  return (
    <MultiCombobox
      className="w-full max-w-[360px]"
      options={options}
      value={[...selected]}
      onChange={(value) => mutation.mutate(value)}
      placeholder="Выберите ИТ-направления"
      searchPlaceholder="Найти направление…"
      emptyText="Направления не найдены"
      loading={directionsQuery.isLoading}
      disabled={mutation.isPending}
      ariaLabel="ИТ-направления продукта"
    />
  );
}

/** Карточка продукта (ux.md §9.3, redesign.md §6.5): DL-описание, программы, заявки. */
export function ProductDetailPage(): ReactNode {
  const { id = '' } = useParams<'id'>();
  const { hasPermission } = useAuth();

  const productQuery = useQuery({
    queryKey: ['product', id],
    queryFn: ({ signal }) => getProduct(id, signal),
    enabled: Boolean(id),
  });
  const programsQuery = useQuery({
    queryKey: ['product-programs', id],
    queryFn: ({ signal }) => listProgramsByProduct(id, signal),
    enabled: Boolean(id),
  });
  const requestsQuery = useQuery({
    queryKey: ['product-requests', id],
    queryFn: ({ signal }) => listRequests({ product_id: id }, { limit: 50, signal }),
    enabled: Boolean(id),
  });

  if (productQuery.isLoading) return <LoadingState rows={8} />;
  if (productQuery.isError || !productQuery.data) {
    return (
      <ErrorState
        error={productQuery.error}
        title="Не удалось загрузить продукт"
        onRetry={() => void productQuery.refetch()}
      />
    );
  }
  const product = productQuery.data;
  const programs = programsQuery.data?.items ?? [];
  const requests = requestsQuery.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={product.name}
        backTo="/registry/products"
        meta={
          <>
            <Badge variant="secondary">{product.code}</Badge>
            {product.is_active ? (
              <StatusBadge status="success">Активен</StatusBadge>
            ) : (
              <StatusBadge status="draft">Неактивен</StatusBadge>
            )}
          </>
        }
      />
      <StaggerGrid className="grid grid-cols-12 gap-4">
        <div className="col-span-12 flex flex-col gap-4 lg:col-span-6">
          <StaggerItem>
            <SectionCard title="О продукте">
              <DL labelWidth={140}>
                <DLRow label="Тип">
                  {product.product_type
                    ? (PRODUCT_TYPE_LABELS[product.product_type] ?? product.product_type)
                    : '—'}
                </DLRow>
                <DLRow label="ИТ-направления">
                  <ProductDirections product={product} />
                </DLRow>
                <DLRow label="Описание">{formatText(product.description)}</DLRow>
              </DL>
            </SectionCard>
          </StaggerItem>
          <StaggerItem>
            <SectionCard title="Программы продукта">
              {programsQuery.isLoading ? (
                <LoadingState rows={3} card={false} />
              ) : programs.length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">Программ пока нет</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {programs.map((program) => (
                    <MiniCard
                      key={program.id}
                      to={`/registry/programs/${program.id}`}
                      icon={GraduationCap}
                      title={program.name}
                      description={
                        <>
                          {program.direction ? `${program.direction.name} · ` : ''}
                          Приоритет {program.priority_rank}
                          {program.starts_on ? ` · старт ${formatDate(program.starts_on)}` : ''}
                          {program.seats ? ` · мест: ${program.seats}` : ''}
                        </>
                      }
                      extra={
                        program.published_to_cms ? (
                          <StatusBadge status="progress" size="sm">
                            на сайте
                          </StatusBadge>
                        ) : undefined
                      }
                    />
                  ))}
                </div>
              )}
            </SectionCard>
          </StaggerItem>
        </div>
        <div className="col-span-12 lg:col-span-6">
          <StaggerItem>
            <SectionCard
              title={`Заявки по продукту${requestsQuery.data ? ` (${requestsQuery.data.total})` : ''}`}
            >
              {requestsQuery.isLoading ? (
                <LoadingState rows={3} card={false} />
              ) : requests.length === 0 ? (
                <p className="py-2 text-sm text-muted-foreground">Заявок по продукту нет</p>
              ) : (
                <div className="flex flex-col gap-2">
                  {requests.map((request) => (
                    <MiniCard
                      key={request.id}
                      to={`/requests/${request.id}`}
                      icon={FileText}
                      title={request.title}
                      description={
                        hasPermission('requests:write')
                          ? (request.assignee?.full_name ?? 'Не назначен')
                          : undefined
                      }
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
