import { ChevronRight } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SlaAttention as Attention } from '@/shared/api/endpoints/sla';
import { Skeleton } from '@/shared/ui/skeleton';
import { KamAvatar } from '../KamActivityParts';
import { formatPct, ofText, plural } from './slaFormat';

/**
 * «Просрочек больше, чем по команде, или старше 30 дней»: КАМы по правилу отбора из
 * API (доля собственных просрочек ≥ 1,5 × команды при нагрузке от 3 заявок, или
 * собственная просрочка больше 30 дней; полученные уже просроченными не учитываются).
 * Карточка — факты без оценок; клик открывает разбор КАМа. Правило — мелким текстом.
 */

interface SlaAttentionProps {
  attention: Attention | null;
  /** Сколько КАМов под правилом (список ограничен 8 строками). */
  matched: number;
  onSelectKam: (kamId: string) => void;
  loading?: boolean;
}

export function SlaAttention({ attention, matched, onSelectKam, loading = false }: SlaAttentionProps): ReactNode {
  const total = attention?.total_kams ?? 0;
  const items = attention?.items ?? [];
  const count = Math.max(matched, items.length);
  return (
    <section className="flex h-full min-w-0 flex-col rounded-lg border border-border bg-card shadow-card">
      <header className="px-5 pb-2 pt-4">
        <h2 className="text-pretty text-base font-semibold">
          Просрочек больше, чем по команде, или старше 30 дней
        </h2>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {loading || !attention
            ? 'Загрузка…'
            : `${count} из ${total} ${plural(total, ['КАМа', 'КАМов', 'КАМов'])}${
                attention.team_own_overdue_pct !== null
                  ? ` · по команде ${formatPct(attention.team_own_overdue_pct)} собственных просрочек`
                  : ''
              }`}
        </p>
      </header>
      <div className="min-w-0 flex-1 px-3 pb-3">
        {loading || !attention ? (
          <div className="flex flex-col gap-2 px-2" aria-hidden="true">
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
            <Skeleton className="h-16 rounded-md" />
          </div>
        ) : items.length === 0 ? (
          <p className="px-2 py-4 text-sm text-muted-foreground">Нет КАМов, подходящих под правило.</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {items.map((item) => (
              <li key={item.kam_id}>
                <button
                  type="button"
                  onClick={() => onSelectKam(item.kam_id)}
                  className="group flex w-full items-start gap-3 rounded-md border border-border bg-card p-3 text-left transition-[border-color,box-shadow] duration-150 hover:border-primary/40 hover:shadow-card focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  aria-label={`${item.kam}: открыть разбор`}
                >
                  <KamAvatar name={item.kam} />
                  <span className="flex min-w-0 flex-1 flex-col gap-1">
                    <span className="flex flex-wrap items-baseline justify-between gap-x-2">
                      <span className="truncate text-sm font-medium group-hover:text-primary">{item.kam}</span>
                      <span className="tabular text-xs font-medium text-status-warning-deep">
                        {ofText(item.overdue, item.active)} · {formatPct(item.overdue_pct)}
                      </span>
                    </span>
                    {item.facts.length > 0 ? (
                      <ul className="flex flex-col gap-0.5 text-xs text-muted-foreground">
                        {item.facts.map((fact) => (
                          <li key={fact} className="text-pretty">
                            {fact}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </span>
                  <ChevronRight
                    className="mt-1 size-4 shrink-0 text-muted-foreground group-hover:text-primary"
                    aria-hidden="true"
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
        {attention?.rule ? (
          <p className="mt-3 px-2 text-pretty text-[11px] leading-4 text-muted-foreground">{attention.rule}</p>
        ) : null}
      </div>
    </section>
  );
}
