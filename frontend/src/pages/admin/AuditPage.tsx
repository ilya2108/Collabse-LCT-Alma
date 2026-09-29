import { Eye } from 'lucide-react';
import { useEffect, useMemo, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useOnboarding } from '@/features/onboarding';
import { listAuditLog, type AuditLogFilters } from '@/shared/api/endpoints/admin';
import type { AuditLogRecord, ListEnvelope } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { dayjs } from '@/shared/lib/dayjs';
import { formatDateTime } from '@/shared/lib/format';
import {
  DataTable,
  DateRangePicker,
  FilterSelect,
  PageHeader,
  StatusBadge,
  type DataTableColumn,
  type RegistryFetchParams,
} from '@/shared/ui/data-table';

/**
 * Журнал аудита (ux.md §14.6, redesign.md §6.9) на DataTable: фильтры по
 * действию/дате/типу объекта и быстрый чип «Раскрытия ПДн»
 * (`student.pii_revealed`, StatusBadge talent кликабельный) — ответ по 152-ФЗ.
 */

/** Русские подписи известных действий (сервер может писать и другие). */
const ACTION_LABELS: Record<string, string> = {
  'auth.login': 'Вход в систему',
  'request.transitioned': 'Переход заявки',
  'request.created': 'Создание заявки',
  'request.assigned': 'Назначение ответственного',
  'workflow.change_requested': 'Запрос изменения процесса',
  'workflow.change_applied': 'Публикация схемы процесса',
  'workflow.change_rejected': 'Отклонение изменения процесса',
  'student.pii_revealed': 'Раскрытие ПДн студента',
  'student.exported': 'Экспорт студентов',
  'flag.updated': 'Изменение фичефлага',
  'import.applied': 'Импорт данных',
  'user.tg_linked': 'Привязка Telegram',
  'program.priority_changed': 'Изменение приоритета программы',
};

const ENTITY_LABELS: Record<string, string> = {
  deal: 'заявка',
  student: 'студент',
  workflow: 'процесс',
  university: 'вуз',
  contract: 'договор',
  program: 'программа',
  app_user: 'пользователь',
  feature_flag: 'фичефлаг',
  import_session: 'импорт',
};

const PII_ACTION = 'student.pii_revealed';

function entityLink(record: AuditLogRecord): ReactNode {
  const label = ENTITY_LABELS[record.entity_type] ?? record.entity_type;
  if (!record.entity_id) return label;
  if (record.entity_type === 'deal') {
    return (
      <Link to={`/requests/${record.entity_id}`} className="text-primary hover:underline">
        {label}
      </Link>
    );
  }
  if (record.entity_type === 'student') {
    return (
      <Link to={`/talent-pool/students/${record.entity_id}`} className="text-primary hover:underline">
        {label}
      </Link>
    );
  }
  return (
    <span>
      {label}{' '}
      <span className="tabular text-xs text-muted-foreground">{record.entity_id.slice(0, 8)}</span>
    </span>
  );
}

const columns: DataTableColumn<AuditLogRecord>[] = [
  {
    key: 'created_at',
    title: 'Время',
    width: 170,
    alwaysVisible: true,
    render: (_v, r) => <span className="tabular">{formatDateTime(r.created_at)}</span>,
  },
  {
    key: 'actor',
    title: 'Пользователь',
    render: (_v, r) => r.actor?.full_name ?? 'Система',
  },
  {
    key: 'action',
    title: 'Действие',
    render: (_v, r) =>
      r.action === PII_ACTION ? (
        <StatusBadge status="talent" icon={Eye}>
          {ACTION_LABELS[PII_ACTION]}
        </StatusBadge>
      ) : (
        (ACTION_LABELS[r.action] ?? r.action)
      ),
  },
  { key: 'entity', title: 'Объект', render: (_v, r) => entityLink(r) },
  {
    key: 'ip',
    title: 'IP',
    width: 130,
    responsive: ['lg'],
    render: (_v, r) => <span className="tabular">{r.ip ?? '—'}</span>,
  },
];

export function AdminAuditPage(): ReactNode {
  /**
   * Адаптер fetcher: filters → AuditLogFilters; поиск по пользователю —
   * клиентский по загруженной странице (actor_id по имени в контракте нет).
   */
  const fetcher = useMemo(
    () =>
      async (params: RegistryFetchParams): Promise<ListEnvelope<AuditLogRecord>> => {
        const apiFilters: AuditLogFilters = {
          action: (params.filters.action as string | undefined) || undefined,
          entity_type: (params.filters.entity_type as string | undefined) || undefined,
          from: params.filters.from
            ? dayjs(params.filters.from as string).startOf('day').toISOString()
            : undefined,
          to: params.filters.to
            ? dayjs(params.filters.to as string).endOf('day').toISOString()
            : undefined,
        };
        const envelope = await listAuditLog(apiFilters, {
          limit: params.limit,
          offset: params.offset,
          signal: params.signal,
        });
        const term = params.search?.trim().toLowerCase() ?? '';
        if (!term) return envelope;
        return {
          ...envelope,
          items: envelope.items.filter((r) =>
            (r.actor?.full_name ?? '').toLowerCase().includes(term),
          ),
        };
      },
    [],
  );

  // Чек-лист онбординга §7.5: открытие аудита (no-op без провайдера).
  const { completeChecklistItem } = useOnboarding();
  useEffect(() => {
    completeChecklistItem('open-audit');
  }, [completeChecklistItem]);

  return (
    <div data-tour="admin-audit">
      <PageHeader title="Журнал аудита" />
      <DataTable<AuditLogRecord>
        screen="admin.audit"
        columns={columns}
        rowKey="id"
        fetcher={fetcher}
        searchPlaceholder="Пользователь (на странице)"
        renderFilters={({ filters, setFilter }) => {
          const piiActive = filters.action === PII_ACTION;
          return (
            <>
              <button
                type="button"
                aria-pressed={piiActive}
                onClick={() => setFilter('action', piiActive ? undefined : PII_ACTION)}
                className={cn(
                  'inline-flex h-9 items-center gap-1.5 rounded-md border px-3 text-sm font-medium transition-[color,background-color,border-color] duration-150',
                  piiActive
                    ? 'border-status-talent bg-status-talent-tint text-status-talent-deep'
                    : 'border-input bg-card text-muted-foreground hover:bg-status-talent-tint hover:text-status-talent-deep',
                )}
              >
                <Eye className="size-4" aria-hidden="true" />
                Раскрытия ПДн
              </button>
              <FilterSelect
                placeholder="Действие"
                allLabel="Действие: любое"
                className="min-w-[200px]"
                options={Object.entries(ACTION_LABELS).map(([value, label]) => ({ value, label }))}
                value={filters.action as string | undefined}
                onChange={(value) => setFilter('action', value)}
              />
              <FilterSelect
                placeholder="Тип объекта"
                allLabel="Объект: любой"
                options={Object.entries(ENTITY_LABELS).map(([value, label]) => ({ value, label }))}
                value={filters.entity_type as string | undefined}
                onChange={(value) => setFilter('entity_type', value)}
              />
              <DateRangePicker
                value={{
                  from: (filters.from as string | undefined) ?? null,
                  to: (filters.to as string | undefined) ?? null,
                }}
                onChange={(range) => {
                  setFilter('from', range.from ?? undefined);
                  setFilter('to', range.to ?? undefined);
                }}
              />
            </>
          );
        }}
        emptyIllustration="search-empty"
        emptyTitle="Событий пока нет"
        emptyDescription="Журнал наполняется входами, переходами заявок и раскрытиями ПДн"
      />
    </div>
  );
}
