import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BookOpen,
  Building2,
  Check,
  ChevronRight,
  FileSignature,
  Loader2,
  Package,
  RotateCw,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ContractSelect, ProductSelect, ProgramSelect } from '@/components/selects/EntitySelects';
import { getContract } from '@/shared/api/endpoints/contracts';
import {
  listIntegrationEvents,
  retryIntegrationEvent,
} from '@/shared/api/endpoints/integrationEvents';
import { listInteractionTypes } from '@/shared/api/endpoints/interactionTypes';
import { getProduct } from '@/shared/api/endpoints/products';
import { getProgram } from '@/shared/api/endpoints/programs';
import { updateRequest } from '@/shared/api/endpoints/requests';
import { getUniversity } from '@/shared/api/endpoints/universities';
import type { IntegrationEventRecord, RequestItem } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import type { SemanticStatus } from '@/shared/config/tokens';
import { formatMoney, formatText } from '@/shared/lib/format';
import { toastSuccess } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { InlineEdit } from '@/shared/ui/inline-edit';
import { Label } from '@/shared/ui/label';
import { SemanticBadge } from './badges';

/**
 * Блоки «Связи / Атрибуты / Интеграции» карточки заявки (ux.md §8.1,
 * redesign.md §6.4): связи — мини-карточки с иконкой и стрелкой, атрибуты —
 * InlineEdit (optimistic PATCH c version), интеграции — строки
 * Check/RotateCw/AlertTriangle + retry для admin.
 */

interface RequestSidebarProps {
  request: RequestItem;
  canEdit: boolean;
}

/** Словарь статусов договора (прежний StatusTag.tsx) + семантический тон. */
const CONTRACT_STATUS_META: Record<string, { label: string; tone: SemanticStatus }> = {
  draft: { label: 'Черновик', tone: 'draft' },
  negotiation: { label: 'Переговоры', tone: 'progress' },
  active: { label: 'Действует', tone: 'success' },
  completed: { label: 'Завершён', tone: 'draft' },
  terminated: { label: 'Расторгнут', tone: 'danger' },
};

// --- Привязка связей -----------------------------------------------------------

function LinkEntitiesDialog({
  request,
  open,
  onClose,
}: {
  request: RequestItem;
  open: boolean;
  onClose: () => void;
}): ReactNode {
  const queryClient = useQueryClient();
  const [contractId, setContractId] = useState<string | undefined>(request.contract_id ?? undefined);
  const [productId, setProductId] = useState<string | undefined>(request.product_id ?? undefined);
  const [programId, setProgramId] = useState<string | undefined>(request.program_id ?? undefined);

  const mutation = useMutation({
    mutationFn: () =>
      updateRequest(request.id, {
        contract_id: contractId ?? null,
        product_id: productId ?? null,
        program_id: programId ?? null,
        version: request.version,
      }),
    onSuccess: () => {
      toastSuccess('Связи заявки обновлены');
      void queryClient.invalidateQueries({ queryKey: ['request', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      onClose();
    },
  });

  // AntD-дропдауны рендерим внутри Radix-диалога, иначе клики гасятся оверлеем
  const popupInDialog = (trigger: HTMLElement): HTMLElement => trigger.parentElement ?? document.body;

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="sm:max-w-[440px]" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Привязать договор и продукты</DialogTitle>
        </DialogHeader>
        <div className="grid gap-4">
          {request.workflow_type === 'b2b' ? (
            <div className="grid gap-2">
              <Label>Договор</Label>
              <ContractSelect
                universityId={request.university_id}
                value={contractId}
                onChange={setContractId}
                getPopupContainer={popupInDialog}
              />
            </div>
          ) : null}
          <div className="grid gap-2">
            <Label>Продукт</Label>
            <ProductSelect value={productId} onChange={setProductId} getPopupContainer={popupInDialog} />
          </div>
          <div className="grid gap-2">
            <Label>Программа</Label>
            <ProgramSelect
              productId={productId ?? null}
              value={programId}
              onChange={setProgramId}
              getPopupContainer={popupInDialog}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
          <Button disabled={mutation.isPending} onClick={() => mutation.mutate()}>
            {mutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
            {mutation.isPending ? 'Сохраняем…' : 'Сохранить'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --- Мини-карточка связи (§6.4) ---------------------------------------------------

function LinkCard({
  icon: Icon,
  label,
  name,
  to,
  extra,
}: {
  icon: LucideIcon;
  label: string;
  name: string | null;
  to?: string;
  extra?: ReactNode;
}): ReactNode {
  const body = (
    <>
      <span
        aria-hidden="true"
        className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary-tint text-primary"
      >
        <Icon className="size-4" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-xs text-muted-foreground">{label}</span>
        <span className="flex items-center gap-1.5">
          <span className="min-w-0 truncate text-sm font-medium">{name ?? '—'}</span>
          {extra}
        </span>
      </span>
      {to && name ? (
        <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      ) : null}
    </>
  );
  if (to && name) {
    return (
      <Link
        to={to}
        className="flex items-center gap-3 rounded-md border p-2.5 transition-[background-color,border-color] duration-150 hover:border-primary/40 hover:bg-primary-tint"
      >
        {body}
      </Link>
    );
  }
  return <div className="flex items-center gap-3 rounded-md border border-dashed p-2.5 opacity-80">{body}</div>;
}

// --- Интеграции ------------------------------------------------------------------

function syncMeta(
  linked: boolean,
  latest: IntegrationEventRecord | undefined,
): { icon: LucideIcon; tone: SemanticStatus; label: string; spinning?: boolean } {
  if (latest) {
    if (latest.status === 'dead')
      return { icon: AlertTriangle, tone: 'danger', label: 'ошибка' };
    if (latest.status === 'pending' || latest.status === 'retrying')
      return { icon: RotateCw, tone: 'progress', label: 'в очереди', spinning: true };
    return { icon: Check, tone: 'success', label: 'синхронизировано' };
  }
  if (linked) return { icon: Check, tone: 'success', label: 'связана' };
  return { icon: Check, tone: 'draft', label: 'нет обмена' };
}

function IntegrationsSection({ request }: { request: RequestItem }): ReactNode {
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const canSeeLog = hasRole('admin', 'observer');
  const eventsQuery = useQuery({
    queryKey: ['request-integration-events', request.id],
    queryFn: ({ signal }) =>
      listIntegrationEvents({ entity_id: request.id }, { limit: 50, signal }),
    enabled: canSeeLog,
  });
  const retryMutation = useMutation({
    mutationFn: retryIntegrationEvent,
    onSuccess: () => {
      toastSuccess('Повторная доставка запущена');
      void queryClient.invalidateQueries({ queryKey: ['request-integration-events', request.id] });
    },
  });

  const events = eventsQuery.data?.items ?? [];
  const latestFor = (system: 'lms' | 'cms'): IntegrationEventRecord | undefined =>
    events.find((e) => e.system === system);

  const rows: Array<{ system: 'lms' | 'cms'; label: string; linked: boolean; externalId: string | null }> = [
    {
      system: 'lms',
      label: 'LMS',
      linked: Boolean(request.external_refs?.lms_enrollment_id),
      externalId: request.external_refs?.lms_enrollment_id ?? null,
    },
    {
      system: 'cms',
      label: 'CMS',
      linked: Boolean(request.external_refs?.cms_lead_id),
      externalId: request.external_refs?.cms_lead_id ?? null,
    },
  ];

  return (
    <div className="space-y-2">
      {rows.map(({ system, label, linked, externalId }) => {
        const latest = latestFor(system);
        const meta = syncMeta(linked, latest);
        return (
          <div key={system} className="flex flex-wrap items-center gap-2">
            <span className="w-9 text-sm font-medium">{label}</span>
            <SemanticBadge status={meta.tone} icon={meta.icon}>
              {meta.label}
            </SemanticBadge>
            {externalId ? (
              <span className="min-w-0 truncate text-xs text-muted-foreground">{externalId}</span>
            ) : null}
            {hasRole('admin') && latest?.status === 'dead' ? (
              <Button
                variant="outline"
                size="sm"
                disabled={retryMutation.isPending}
                onClick={() => retryMutation.mutate(latest.id)}
              >
                <RotateCw aria-hidden="true" />
                Повторить
              </Button>
            ) : null}
          </div>
        );
      })}
      {canSeeLog ? (
        <Link
          to="/admin/integrations"
          className="inline-block text-xs text-muted-foreground hover:text-primary hover:underline"
        >
          Журнал обмена — в мониторе интеграций →
        </Link>
      ) : null}
    </div>
  );
}

// --- Сайдбар ------------------------------------------------------------------------

export function RequestSidebar({ request, canEdit }: RequestSidebarProps): ReactNode {
  const queryClient = useQueryClient();
  const [linkOpen, setLinkOpen] = useState(false);

  const universityQuery = useQuery({
    queryKey: ['university', request.university_id],
    queryFn: ({ signal }) => getUniversity(request.university_id as string, signal),
    enabled: Boolean(request.university_id) && !request.university,
  });
  const contractQuery = useQuery({
    queryKey: ['contract', request.contract_id],
    queryFn: ({ signal }) => getContract(request.contract_id as string, signal),
    enabled: Boolean(request.contract_id),
  });
  const productQuery = useQuery({
    queryKey: ['product', request.product_id],
    queryFn: ({ signal }) => getProduct(request.product_id as string, signal),
    enabled: Boolean(request.product_id) && !request.product,
  });
  const programQuery = useQuery({
    queryKey: ['program', request.program_id],
    queryFn: ({ signal }) => getProgram(request.program_id as string, signal),
    enabled: Boolean(request.program_id) && !request.program,
  });
  const interactionTypesQuery = useQuery({
    queryKey: ['interaction-types'],
    queryFn: ({ signal }) => listInteractionTypes(signal),
    enabled: request.workflow_type === 'b2c',
    staleTime: 60_000,
  });

  const attributesMutation = useMutation({
    mutationFn: (patch: { amount?: string | null; description?: string | null }) =>
      updateRequest(request.id, { ...patch, version: request.version }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['request', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
    },
  });

  const universityName = request.university?.name ?? universityQuery.data?.name ?? null;
  const productName = request.product?.name ?? productQuery.data?.name ?? null;
  const programName = request.program?.name ?? programQuery.data?.name ?? null;
  const interactionTypeName = interactionTypesQuery.data?.items.find(
    (t) => t.id === request.interaction_type_id,
  )?.name;

  return (
    <aside className="rounded-lg border bg-card shadow-card">
      {/* Связи */}
      <section className="p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h3 className="m-0 text-base font-semibold">Связи</h3>
          {canEdit ? (
            <Button variant="ghost" size="sm" onClick={() => setLinkOpen(true)}>
              Привязать…
            </Button>
          ) : null}
        </div>
        <div className="space-y-2">
          {request.workflow_type === 'b2b' ? (
            <LinkCard
              icon={Building2}
              label="Вуз"
              name={request.university_id ? (universityName ?? '…') : null}
              to={request.university_id ? `/registry/universities/${request.university_id}` : undefined}
            />
          ) : (
            <div className="rounded-md border p-2.5">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Контрагент</span>
                {request.client_kind ? (
                  <Badge variant="outline">
                    {request.client_kind === 'person' ? 'физлицо' : 'юрлицо'}
                  </Badge>
                ) : null}
              </div>
              <div className="text-sm font-medium">
                {request.client?.company_name ?? request.client?.full_name ?? '—'}
              </div>
              <dl className="mt-2 grid grid-cols-[72px_1fr] gap-y-1 text-sm">
                {request.client?.email ? (
                  <>
                    <dt className="text-muted-foreground">Email</dt>
                    <dd className="m-0 truncate">{request.client.email}</dd>
                  </>
                ) : null}
                {request.client?.phone ? (
                  <>
                    <dt className="text-muted-foreground">Телефон</dt>
                    <dd className="m-0 truncate">{request.client.phone}</dd>
                  </>
                ) : null}
                <dt className="text-muted-foreground">Тип</dt>
                <dd className="m-0 truncate">{formatText(interactionTypeName ?? null)}</dd>
              </dl>
            </div>
          )}
          <LinkCard
            icon={FileSignature}
            label="Договор"
            name={request.contract_id ? (contractQuery.data?.number ?? '…') : null}
            to={request.contract_id ? `/registry/contracts/${request.contract_id}` : undefined}
            extra={
              contractQuery.data ? (
                <SemanticBadge
                  status={CONTRACT_STATUS_META[contractQuery.data.status]?.tone ?? 'draft'}
                >
                  {CONTRACT_STATUS_META[contractQuery.data.status]?.label ?? contractQuery.data.status}
                </SemanticBadge>
              ) : undefined
            }
          />
          <LinkCard
            icon={Package}
            label="Продукт"
            name={request.product_id ? (productName ?? '…') : null}
            to={request.product_id ? `/registry/products/${request.product_id}` : undefined}
          />
          <LinkCard
            icon={BookOpen}
            label="Программа"
            name={request.program_id ? (programName ?? '…') : null}
            to={request.program_id ? `/registry/programs/${request.program_id}` : undefined}
          />
        </div>
      </section>

      {/* Атрибуты — InlineEdit (§3.10) */}
      <section className="border-t p-4">
        <h3 className="m-0 mb-3 text-base font-semibold">Атрибуты</h3>
        <dl className="grid grid-cols-[110px_1fr] items-center gap-y-1 text-sm">
          <dt className="text-muted-foreground">Сумма</dt>
          <dd className="m-0 min-w-0">
            <InlineEdit
              type="number"
              value={request.amount != null ? Number(request.amount) : null}
              formatter={(v) => (v == null ? null : formatMoney(Number(v)))}
              label="Изменить сумму"
              placeholder="Указать сумму…"
              disabled={!canEdit}
              onSave={(v) =>
                attributesMutation.mutate({ amount: v == null ? null : Number(v).toFixed(2) })
              }
            />
          </dd>
          <dt className="self-start pt-1.5 text-muted-foreground">Примечание</dt>
          <dd className="m-0 min-w-0">
            <InlineEdit
              type="text"
              value={request.description}
              label="Изменить примечание"
              placeholder="Добавить примечание…"
              disabled={!canEdit}
              onSave={(v) =>
                attributesMutation.mutate({
                  description: typeof v === 'string' && v.trim() ? v.trim() : null,
                })
              }
            />
          </dd>
        </dl>
      </section>

      {/* Интеграции */}
      <section className="border-t p-4">
        <h3 className="m-0 mb-3 text-base font-semibold">Интеграции</h3>
        <IntegrationsSection request={request} />
      </section>

      <LinkEntitiesDialog
        key={linkOpen ? 'open' : 'closed'}
        request={request}
        open={linkOpen}
        onClose={() => setLinkOpen(false)}
      />
    </aside>
  );
}
