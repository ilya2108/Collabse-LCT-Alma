import { ChevronDown } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type { SlaStageRow, SlaWorkflowSummary } from '@/shared/api/endpoints/sla';
import type { WorkflowType } from '@/shared/api/types';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { StageTag } from '@/shared/ui/data-table';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { ChartCard } from '../../dashboard/ChartCard';
import { Segmented } from '../../dashboard/Segmented';
import { SOURCE_SLA, rowsHeight, stageOverdueOption } from './slaCharts';
import {
  BUCKET_KEYS,
  bucketLabel,
  formatDays,
  formatDelay,
  formatPct,
  normSourceLabel,
  ofText,
} from './slaFormat';

/**
 * «Где застревают»: этапы процесса в порядке схемы — столбец = просроченные на этапе,
 * сегменты = корзины старения; переключатель «Вузы | Клиенты». Под графиком —
 * сворачиваемая таблица этапов с нормой и её источником. Клик по этапу (только для
 * текущего периода) открывает доску с фильтром «Зависшие» на этом этапе.
 */

const WF_SHORT: Record<WorkflowType, string> = { b2b: 'Вузы', b2c: 'Клиенты' };

interface SlaStagesCardProps {
  stages: Partial<Record<WorkflowType, SlaStageRow[]>>;
  byWorkflow: Partial<Record<WorkflowType, SlaWorkflowSummary>>;
  caption: (source: string) => string;
  exportCaption?: string[];
  isCurrent: boolean;
  compact: boolean;
  loading?: boolean;
}

export function SlaStagesCard({
  stages,
  byWorkflow,
  caption,
  exportCaption,
  isCurrent,
  compact,
  loading = false,
}: SlaStagesCardProps): ReactNode {
  const navigate = useNavigate();
  const workflows = (['b2b', 'b2c'] as WorkflowType[]).filter((wf) => (stages[wf]?.length ?? 0) > 0);
  const [picked, setPicked] = useState<WorkflowType | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const wf: WorkflowType | undefined = picked && workflows.includes(picked) ? picked : workflows[0];
  const rows = wf ? (stages[wf] ?? []) : [];

  const subtitle = (['b2b', 'b2c'] as WorkflowType[])
    .map((key) => {
      const summary = byWorkflow[key];
      return summary ? `${WF_SHORT[key]}: ${ofText(summary.overdue, summary.active)}` : null;
    })
    .filter(Boolean)
    .join(' · ');

  const switcher =
    workflows.length > 1 ? (
      <Segmented
        size="sm"
        aria-label="Процесс"
        value={wf ?? 'b2b'}
        onChange={(value) => setPicked(value as WorkflowType)}
        options={workflows.map((key) => ({
          value: key,
          label: WF_SHORT[key],
          count: byWorkflow[key]?.overdue,
        }))}
      />
    ) : null;

  return (
    <div className="flex h-full min-w-0 flex-col gap-2">
      <ChartCard
        chartId="sla-stages"
        title="Где застревают"
        subtitle={
          subtitle
            ? `${subtitle} — просрочено из заявок в работе, этап на дату среза`
            : 'Просроченные заявки по этапам на дату среза'
        }
        exportCaption={exportCaption}
        loading={loading}
        height={rowsHeight(rows.length, compact ? 220 : 280)}
        empty={rows.length === 0}
        emptyText="Заявок в работе на этапах нет"
        option={rows.length > 0 ? stageOverdueOption(rows, caption(SOURCE_SLA), compact) : null}
        extra={switcher}
        onSegmentClick={
          isCurrent && wf
            ? ({ dataKey }) => {
                const row = rows.find((item) => item.id === dataKey);
                if (row && !row.deleted) {
                  navigate(`/board?wf=${wf}&status=${encodeURIComponent(row.code)}&stuck=true`);
                }
              }
            : undefined
        }
      />
      {rows.length > 0 ? (
        <Collapsible open={tableOpen} onOpenChange={setTableOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="h-8 self-start px-2 text-muted-foreground">
              Таблица этапов
              <ChevronDown
                className={tableOpen ? 'rotate-180 transition-transform' : 'transition-transform'}
                aria-hidden="true"
              />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-1">
            <StagesTable rows={rows} />
          </CollapsibleContent>
        </Collapsible>
      ) : null}
    </div>
  );
}

function StagesTable({ rows }: { rows: SlaStageRow[] }): ReactNode {
  return (
    <div className="max-w-full overflow-x-auto rounded-lg border border-border bg-card">
      <Table className="min-w-[760px]">
        <TableHeader>
          <TableRow>
            <TableHead>Этап</TableHead>
            <TableHead>Норма</TableHead>
            <TableHead className="text-right">Просрочено</TableHead>
            {BUCKET_KEYS.map((key) => (
              <TableHead key={key} className="text-right">
                {bucketLabel(key)}
              </TableHead>
            ))}
            <TableHead className="text-right">Обычно сверх нормы</TableHead>
            <TableHead className="text-right">Уложились в норму</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => (
            <TableRow key={row.id}>
              <TableCell>
                <StageTag size="sm" color={row.color} name={row.name} />
              </TableCell>
              <TableCell className="tabular whitespace-nowrap">
                {formatDays(row.norm_days)}
                <span className="block text-xs text-muted-foreground">
                  {normSourceLabel(row.norm_source)}
                </span>
              </TableCell>
              <TableCell className="tabular text-right">
                {ofText(row.overdue, row.active)}
                {row.overdue_pct !== null ? (
                  <span className="block text-xs text-muted-foreground">{formatPct(row.overdue_pct)}</span>
                ) : null}
              </TableCell>
              {BUCKET_KEYS.map((key) => (
                <TableCell key={key} className="tabular text-right">
                  {row.aging[key] ? row.aging[key] : <span className="text-muted-foreground">—</span>}
                </TableCell>
              ))}
              <TableCell className="tabular text-right">{formatDelay(row.delay_median_days)}</TableCell>
              <TableCell className="tabular text-right">
                {row.decided > 0 ? (
                  <>
                    {formatPct(row.on_time_pct)}
                    <span className="block text-xs text-muted-foreground">
                      решено {row.decided}
                    </span>
                  </>
                ) : (
                  <span className="text-muted-foreground">—</span>
                )}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
