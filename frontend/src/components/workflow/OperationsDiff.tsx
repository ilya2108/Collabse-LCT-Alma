import { ArrowRightLeft, Pencil, Plus, Trash2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import type {
  WorkflowChangeImpact,
  WorkflowOperation,
  WorkflowSchema,
} from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';

/**
 * Наглядный дифф пакета операций изменения схемы (redesign.md §6.6):
 * строки added/renamed/deleted — плашки success/warning/danger-tint с иконками
 * Plus/Pencil/Trash2, не текстовый список. Используется в модалке отправки
 * конструктора и на экране согласований администратора.
 */

const PATCH_LABELS: Record<string, string> = {
  color: 'цвет',
  stuck_threshold_days: 'порог зависания',
  position: 'порядок на доске',
  is_initial: 'начальный этап',
  is_terminal: 'терминальность',
  terminal_outcome: 'исход',
  triggers_lms_handover: 'передача в LMS',
  ui_position: 'позиция на канве',
  name: 'название',
  kind: 'тип перехода',
  requires_comment: 'обязательный комментарий',
  allowed_roles: 'роли',
};

function patchSummary(patch: Record<string, unknown>): string {
  const keys = Object.keys(patch);
  return keys.map((key) => PATCH_LABELS[key] ?? key).join(', ');
}

interface DiffRow {
  tone: 'add' | 'change' | 'remove';
  text: ReactNode;
}

const TONE_META: Record<DiffRow['tone'], { icon: LucideIcon; label: string; className: string }> = {
  add: {
    icon: Plus,
    label: 'добавлено',
    className: 'bg-status-success-tint text-status-success-deep',
  },
  change: {
    icon: Pencil,
    label: 'изменено',
    className: 'bg-status-warning-tint text-status-warning-deep',
  },
  remove: {
    icon: Trash2,
    label: 'удалено',
    className: 'bg-status-danger-tint text-status-danger-deep',
  },
};

interface OperationsDiffProps {
  operations: WorkflowOperation[];
  /** Живая схема соответствующего workflow — для резолва имён по id. */
  schema?: WorkflowSchema | null;
  /** Impact пакета — счётчики мигрируемых заявок у delete-строк. */
  impact?: WorkflowChangeImpact | null;
}

export function OperationsDiff({ operations, schema, impact }: OperationsDiffProps): ReactNode {
  const statusById = new Map((schema?.statuses ?? []).map((s) => [s.id, s]));
  const statusNameByCode = new Map((schema?.statuses ?? []).map((s) => [s.code, s.name]));
  for (const op of operations) {
    if (op.op === 'add_status') statusNameByCode.set(op.status.code, op.status.name);
  }
  const transitionById = new Map((schema?.transitions ?? []).map((t) => [t.id, t]));

  const statusName = (id: string): string => statusById.get(id)?.name ?? '…';
  const codeName = (code: string): string => statusNameByCode.get(code) ?? code;
  const transitionLabel = (id: string): string => {
    const t = transitionById.get(id);
    if (!t) return '…';
    return `«${t.name}» (${statusName(t.from_status_id)} → ${statusName(t.to_status_id)})`;
  };
  const migratedCount = (statusId: string): number | null => {
    const row = impact?.by_status.find((b) => b.status_id === statusId);
    return row ? row.count : null;
  };

  const rows: DiffRow[] = operations.map((op) => {
    switch (op.op) {
      case 'add_status':
        return { tone: 'add', text: <>Новый этап «{op.status.name}»</> };
      case 'rename_status':
        return {
          tone: 'change',
          text: (
            <>
              Переименование этапа: «{op.confirm_name}» → <b>«{op.new_name}»</b>
            </>
          ),
        };
      case 'update_status':
        return {
          tone: 'change',
          text: (
            <>
              Этап «{statusName(op.status_id)}»: {patchSummary(op.patch)}
            </>
          ),
        };
      case 'delete_status': {
        const count = migratedCount(op.status_id);
        const target =
          impact?.by_status.find((b) => b.status_id === op.status_id)?.migrate_to?.name ??
          statusName(op.migrate_to_status_id);
        return {
          tone: 'remove',
          text: (
            <>
              Удаление этапа «{op.confirm_name}»
              {count !== null ? (
                <>
                  {' — '}
                  <b>{count} заявок</b> будут перенесены в «{target}»
                </>
              ) : (
                <> — заявки будут перенесены в «{target}»</>
              )}
            </>
          ),
        };
      }
      case 'add_transition':
        return {
          tone: 'add',
          text: (
            <>
              Новый переход «{op.transition.name}»: {codeName(op.transition.from_code)} →{' '}
              {codeName(op.transition.to_code)}
              {op.transition.kind === 'return' ? (
                <span className="ml-1.5 inline-flex items-center gap-1 rounded-sm bg-status-danger-tint px-1.5 py-0.5 text-xs font-medium text-status-danger-deep">
                  <ArrowRightLeft className="size-3" aria-hidden="true" />
                  возврат
                </span>
              ) : null}
            </>
          ),
        };
      case 'update_transition':
        return {
          tone: 'change',
          text: (
            <>
              Переход {transitionLabel(op.transition_id)}: {patchSummary(op.patch)}
            </>
          ),
        };
      case 'delete_transition':
        return { tone: 'remove', text: <>Удалён переход {transitionLabel(op.transition_id)}</> };
      default:
        return { tone: 'change', text: <>Операция {(op as { op: string }).op}</> };
    }
  });

  if (rows.length === 0) {
    return <p className="text-sm text-muted-foreground">Изменений нет</p>;
  }

  return (
    <ul className="space-y-1.5">
      {rows.map((row, index) => {
        const meta = TONE_META[row.tone];
        const Icon = meta.icon;
        return (
          <li key={index} className={cn('flex items-start gap-2 rounded-md px-3 py-2', meta.className)}>
            <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            <span className="sr-only">{meta.label}: </span>
            <span className="min-w-0 text-sm text-foreground">{row.text}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Сводка impact: «Будет перенесено N заявок» + разбивка. */
export function ImpactSummary({
  impact,
}: {
  impact: WorkflowChangeImpact | null | undefined;
}): ReactNode {
  if (!impact) return null;
  if (impact.affected_requests_total === 0) {
    return <p className="text-sm text-muted-foreground">Перенос заявок не потребуется</p>;
  }
  return (
    <div className="space-y-1">
      <p className="text-sm font-semibold">
        Будет перенесено заявок: <span className="tabular">{impact.affected_requests_total}</span>
      </p>
      {impact.by_status.map((row) => (
        <p key={row.status_id} className="text-sm text-muted-foreground">
          «{row.status_name ?? row.status_id}» → «{row.migrate_to?.name ?? '…'}»:{' '}
          <span className="tabular">{row.count}</span>
        </p>
      ))}
    </div>
  );
}
