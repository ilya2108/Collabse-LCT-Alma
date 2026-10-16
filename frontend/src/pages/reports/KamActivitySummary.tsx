import {
  Activity,
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  CircleX,
  FilePlus2,
  FileSignature,
  GraduationCap,
  LoaderCircle,
  Percent,
  School,
} from 'lucide-react';
import { motion } from 'motion/react';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type {
  KamActivitySummary as Summary,
  KpiValue,
} from '@/shared/api/endpoints/kamActivity';
import type { WorkflowType } from '@/shared/api/types';
import { formatMoney } from '@/shared/lib/format';
import { useBreakpoint } from '@/shared/lib/useBreakpoint';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import { StatusBadge } from '@/shared/ui/data-table';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { StatCard } from '@/shared/ui/stat-card';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { ChartCard } from '../dashboard/ChartCard';
import { Segmented } from '../dashboard/Segmented';
import { RequestLink } from './KamActivityParts';
import {
  SOURCE_ACTIONS,
  SOURCE_HISTORY,
  SOURCE_TALENT,
  chartCaption,
  formatDay,
  formatPercent,
  interactionsDynamicsOption,
  kamCompareOption,
  rowsHeight,
  stageConversionOption,
  stageDwellOption,
  talentStatusOption,
  universitiesByStageOption,
} from './kamActivityModel';

/**
 * Вкладка «Сводка для заказчика» (адресат Б, ux.md §6.4) — одна страница «где мы с вузами
 * и кадровым резервом», тот же состав, что на стр. 1 PDF:
 * 4 KPI (охват вузов, вузы с действующим договором, обучающиеся, выиграно) с дельтами к
 * прошлому периоду той же длины → главная диаграмма «Вузы B2B по этапам на конец периода»
 * с правилом отнесения → риски и выводы → студенты по программам. Подробная аналитика по
 * заявкам и КАМам (конверсия, «где застревают», динамика, разрез по КАМам, топ зависших)
 * свёрнута ниже. У каждого графика — PNG и подпись периода и источника.
 */

const SOURCE_UNIVERSITIES = 'Альма, вузы и история переходов B2B-заявок';

function deltaOf(kpi: KpiValue | undefined): number | undefined {
  const delta = kpi?.delta;
  return delta === null || delta === undefined || delta === 0 ? undefined : delta;
}

function SectionCard({
  title,
  extra,
  children,
}: {
  title: string;
  extra?: ReactNode;
  children: ReactNode;
}): ReactNode {
  return (
    <section className="flex min-w-0 flex-col rounded-lg border border-border bg-card shadow-card">
      <header className="flex flex-wrap items-center justify-between gap-2 px-5 pb-2 pt-4">
        <h2 className="text-base font-semibold">{title}</h2>
        {extra}
      </header>
      <div className="min-w-0 px-2 pb-3 sm:px-3">{children}</div>
    </section>
  );
}

interface SummaryProps {
  summary: Summary | null;
  loading: boolean;
  onOpenRequest: (id: string) => void;
  onSelectKam: (kamId: string) => void;
  onExportPdf: () => void;
  exporting: boolean;
}

export function KamActivitySummary({
  summary,
  loading,
  onOpenRequest,
  onSelectKam,
  onExportPdf,
  exporting,
}: SummaryProps): ReactNode {
  const navigate = useNavigate();
  const compact = !useBreakpoint().md;
  const stageWorkflows = (Object.keys(summary?.stages ?? {}) as WorkflowType[]).filter(
    (wf) => (summary?.stages[wf]?.length ?? 0) > 0,
  );
  const [stageWf, setStageWf] = useState<WorkflowType | null>(null);
  const activeWf: WorkflowType | undefined =
    stageWf && stageWorkflows.includes(stageWf) ? stageWf : stageWorkflows[0];
  const stageRows = activeWf ? (summary?.stages[activeWf] ?? []) : [];
  const kpi = summary?.kpi;
  const pending = loading || !summary;

  const period = summary?.period;
  const previous = summary?.previous_period;
  const caption = (source: string): string =>
    period ? chartCaption(period, source) : '';
  const stageSwitch =
    stageWorkflows.length > 1 ? (
      <Segmented
        size="sm"
        aria-label="Процесс воронки"
        value={activeWf ?? 'b2b'}
        onChange={(value) => setStageWf(value as WorkflowType)}
        options={stageWorkflows.map((wf) => ({ value: wf, label: wf.toUpperCase() }))}
      />
    ) : null;

  const conversion = kpi?.conversion.value ?? null;
  const assigned = kpi?.universities_assigned.value ?? 0;
  const touched = kpi?.assigned_touched.value ?? 0;
  const byKam = summary?.by_kam ?? [];
  const talent = summary?.talent;
  const byStage = summary?.universities_by_stage ?? null;
  const risks = summary?.risks ?? [];
  const [detailsOpen, setDetailsOpen] = useState(false);

  // главная диаграмма вида Б: этапы + «Без заявок в работе» (если такие вузы есть)
  const stageChartRows = byStage
    ? [
        ...byStage.rows,
        ...(byStage.without_requests.value > 0 || byStage.without_requests.prev > 0
          ? [
              {
                code: 'without_requests',
                name: byStage.without_requests.label,
                color: null,
                value: byStage.without_requests.value,
                prev: byStage.without_requests.prev,
                universities: byStage.without_requests.universities,
              },
            ]
          : []),
      ]
    : [];
  const previousLabel = previous
    ? `Прошлый: ${formatDay(previous.from)} — ${formatDay(previous.to)}`
    : 'Прошлый период';

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          {period && previous
            ? `Сравнение с ${formatDay(previous.from)} — ${formatDay(previous.to)} (та же длина периода)`
            : 'Сравнение с прошлым периодом той же длины'}
        </p>
        <Button onClick={onExportPdf} disabled={exporting}>
          {exporting ? <LoaderCircle className="animate-spin" aria-hidden="true" /> : null}
          Скачать PDF для заказчика
        </Button>
      </div>

      <motion.div
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
        className="grid grid-cols-1 gap-3 min-[440px]:grid-cols-2 xl:grid-cols-4"
      >
        <motion.div variants={staggerItem}>
          <StatCard
            title="Охват вузов"
            icon={School}
            tone="primary"
            value={kpi?.coverage_pct.value ?? 0}
            suffix="%"
            delta={deltaOf(kpi?.coverage_pct)}
            hint={`С контактом ${touched} из ${assigned} закреплённых вузов; дельта — в п.п.`}
            loading={pending}
          />
        </motion.div>
        <motion.div variants={staggerItem}>
          <StatCard
            title="Вузы с договором"
            icon={FileSignature}
            tone="success"
            value={kpi?.universities_with_contract?.value ?? 0}
            delta={deltaOf(kpi?.universities_with_contract)}
            hint="Вузы с действующим на конец периода договором"
            loading={pending}
          />
        </motion.div>
        <motion.div variants={staggerItem}>
          <StatCard
            title="Обучаются"
            icon={GraduationCap}
            tone="talent"
            value={kpi?.students_studying?.value ?? 0}
            delta={deltaOf(kpi?.students_studying)}
            hint="Студенты вузов отчёта в статусе «Обучается» на конец периода"
            loading={pending}
          />
        </motion.div>
        <motion.div variants={staggerItem}>
          <StatCard
            title="Выиграно заявок"
            icon={CircleCheck}
            tone="success"
            value={kpi?.won.value ?? 0}
            delta={deltaOf(kpi?.won)}
            hint={
              conversion === null
                ? 'Вошли в финальный этап «выигрыш» в периоде; закрытых заявок нет'
                : `Вошли в финальный этап «выигрыш» в периоде; конверсия ${formatPercent(conversion)}`
            }
            loading={pending}
          />
        </motion.div>
      </motion.div>

      <div className="grid grid-cols-12 items-start gap-4">
        <div className="col-span-12 flex min-w-0 flex-col gap-2 lg:col-span-7">
          {byStage || pending ? (
            <>
              <ChartCard
                chartId="kam-activity-universities-by-stage"
                title="Вузы B2B по этапам на конец периода"
                loading={pending}
                height={rowsHeight(stageChartRows.length, 300)}
                empty={stageChartRows.every((row) => row.value === 0 && row.prev === 0)}
                emptyText="Вузов в отчёте нет"
                option={
                  stageChartRows.length > 0
                    ? universitiesByStageOption(
                        stageChartRows,
                        caption(SOURCE_UNIVERSITIES),
                        { current: 'Конец периода', previous: previousLabel },
                        compact,
                      )
                    : null
                }
                onSegmentClick={({ dataKey }) => {
                  if (dataKey && dataKey !== 'without_requests') {
                    navigate(`/board?wf=b2b&status=${dataKey}`);
                  }
                }}
              />
              {byStage ? (
                <p className="px-1 text-xs text-muted-foreground">
                  Вузов на конец периода: {byStage.total} (прошлый период — {byStage.total_prev}).{' '}
                  {byStage.rule}
                </p>
              ) : null}
            </>
          ) : (
            <SectionCard title="Вузы по этапам">
              <p className="px-2 pb-1 text-sm text-muted-foreground">
                Диаграмма вузов строится по B2B-заявкам — в отчёте сейчас только B2C.
              </p>
            </SectionCard>
          )}
        </div>
        <div className="col-span-12 flex min-w-0 flex-col gap-4 lg:col-span-5">
          <SectionCard title="Риски">
            {pending ? (
              <div className="shimmer mx-2 h-24 rounded-md" aria-hidden="true" />
            ) : risks.length > 0 ? (
              <ul className="flex flex-col gap-3 px-2 text-sm">
                {risks.map((risk) => (
                  <li key={risk.key} className="flex gap-2">
                    <AlertTriangle
                      className={
                        risk.key === 'lost'
                          ? 'mt-0.5 size-4 shrink-0 text-status-danger-deep'
                          : 'mt-0.5 size-4 shrink-0 text-status-warning-deep'
                      }
                      aria-hidden="true"
                    />
                    <div className="min-w-0">
                      <p className="font-medium">
                        {risk.label}: <span className="tabular">{risk.value}</span>
                      </p>
                      {risk.note ? <p className="text-muted-foreground">{risk.note}</p> : null}
                      {risk.items.length > 0 ? (
                        <p className="text-xs text-muted-foreground">{risk.items.join('; ')}</p>
                      ) : null}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-2 pb-1 text-sm text-muted-foreground">Рисков за период не выявлено.</p>
            )}
          </SectionCard>
          <SectionCard title="Ключевые выводы">
            {pending ? (
              <div className="shimmer mx-2 h-24 rounded-md" aria-hidden="true" />
            ) : (summary?.highlights.length ?? 0) > 0 ? (
              <ul className="flex flex-col gap-2 px-2 text-sm">
                {summary?.highlights.map((line) => (
                  <li key={line} className="flex gap-2">
                    <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" aria-hidden="true" />
                    <span>{line}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-2 text-sm text-muted-foreground">Данных за период нет.</p>
            )}
          </SectionCard>
        </div>
      </div>

      <div className="grid grid-cols-12 items-start gap-4">
        <div className="col-span-12 min-w-0 lg:col-span-5">
          <ChartCard
            chartId="kam-activity-talent"
            title="Студенты на программах (пул талантов)"
            loading={pending}
            height={280}
            empty={(talent?.students_total ?? 0) === 0}
            emptyText="Студентов вузов отчёта нет"
            option={talent ? talentStatusOption(talent, caption(SOURCE_TALENT), compact) : null}
            onSegmentClick={({ dataKey }) =>
              navigate(dataKey ? `/talent-pool?status=${dataKey}` : '/talent-pool')
            }
          />
        </div>
        <div className="col-span-12 min-w-0 lg:col-span-7">
          <SectionCard title="Студенты по программам">
            {talent && talent.programs.length > 0 ? (
              <>
                <div className="max-w-full overflow-x-auto">
                  <Table className="min-w-[560px]">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Программа</TableHead>
                        <TableHead>Вуз</TableHead>
                        <TableHead className="text-right">Обучаются</TableHead>
                        <TableHead className="text-right">Выпускники</TableHead>
                        <TableHead className="text-right">Пул талантов</TableHead>
                        <TableHead className="text-right">Всего</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {talent.programs.map((program) => (
                        <TableRow key={program.program_id}>
                          <TableCell className="font-medium">{program.name ?? '—'}</TableCell>
                          <TableCell>{program.university ?? '—'}</TableCell>
                          <TableCell className="tabular text-right">{program.studying}</TableCell>
                          <TableCell className="tabular text-right">{program.graduate}</TableCell>
                          <TableCell className="tabular text-right font-semibold">
                            {program.talent_pool}
                          </TableCell>
                          <TableCell className="tabular text-right">{program.total}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                <p className="px-2 pt-2 text-xs text-muted-foreground">
                  Статус на конец периода по истории воронки студентов. За период в пул талантов
                  добавлено: {talent.talent_pool_added.value} (прошлый период —{' '}
                  {talent.talent_pool_added.prev}). Только агрегаты, без персональных данных.
                </p>
              </>
            ) : (
              <p className="px-2 pb-1 text-sm text-muted-foreground">
                {pending ? 'Загрузка…' : 'Студентов на программах вузов отчёта нет.'}
              </p>
            )}
          </SectionCard>
        </div>
      </div>

      <Collapsible open={detailsOpen} onOpenChange={setDetailsOpen}>
        <CollapsibleTrigger asChild>
          <Button variant="outline" className="w-full justify-between sm:w-auto">
            Подробно: активность КАМов и заявки по этапам
            <ChevronDown
              className={detailsOpen ? 'rotate-180 transition-transform' : 'transition-transform'}
              aria-hidden="true"
            />
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="flex min-w-0 flex-col gap-4 pt-4">
          <div className="grid grid-cols-1 gap-3 min-[440px]:grid-cols-2 xl:grid-cols-4">
            <StatCard
              title="Новые заявки"
              icon={FilePlus2}
              tone="primary"
              value={kpi?.new_requests.value ?? 0}
              delta={deltaOf(kpi?.new_requests)}
              hint={`На сумму ${formatMoney(kpi?.new_amount.value ?? null)}`}
              loading={pending}
            />
            <StatCard
              title="Отказы"
              icon={CircleX}
              tone="danger"
              value={kpi?.lost.value ?? 0}
              delta={deltaOf(kpi?.lost)}
              invertedDelta
              hint="Вошли в финальный этап «отказ» в периоде"
              loading={pending}
            />
            <StatCard
              title="Конверсия"
              icon={Percent}
              tone="primary"
              value={conversion ?? 0}
              suffix="%"
              delta={deltaOf(kpi?.conversion)}
              hint={
                conversion === null
                  ? 'Закрытых заявок за период нет'
                  : 'Выиграно / (выиграно + отказ); дельта — в п.п.'
              }
              loading={pending}
            />
            <StatCard
              title="Взаимодействия"
              icon={Activity}
              tone="primary"
              value={kpi?.interactions.value ?? 0}
              delta={deltaOf(kpi?.interactions)}
              hint="Собственные действия КАМов: переходы, комментарии, файлы, мероприятия"
              loading={pending}
            />
          </div>

          <div className="grid grid-cols-12 items-start gap-4">
            <div className="col-span-12 min-w-0 lg:col-span-7">
              <ChartCard
                chartId="kam-activity-stages"
                title="Конверсия заявок по этапам"
                loading={pending}
                height={rowsHeight(stageRows.length, 280)}
                empty={stageRows.every((row) => row.reached === 0)}
                option={
                  stageRows.length > 0
                    ? stageConversionOption(stageRows, caption(SOURCE_HISTORY), compact)
                    : null
                }
                extra={stageSwitch}
              />
            </div>
            <div className="col-span-12 min-w-0 lg:col-span-5">
              <ChartCard
                chartId="kam-activity-dwell"
                title="Где застревают"
                loading={pending}
                height={rowsHeight(stageRows.filter((row) => !row.is_terminal).length, 280)}
                empty={stageRows.every((row) => row.avg_days === null)}
                emptyText="Нет переходов в периоде"
                option={
                  stageRows.length > 0
                    ? stageDwellOption(stageRows, caption(SOURCE_HISTORY), compact)
                    : null
                }
                onSegmentClick={({ dataKey }) => {
                  if (dataKey && activeWf) navigate(`/board?wf=${activeWf}&status=${dataKey}`);
                }}
              />
            </div>
            <div className="col-span-12 min-w-0">
              <ChartCard
                chartId="kam-activity-dynamics"
                title="Динамика взаимодействий"
                loading={pending}
                empty={(summary?.dynamics.series ?? []).every((s) =>
                  s.points.every((p) => p.value === 0),
                )}
                option={
                  summary
                    ? interactionsDynamicsOption(
                        summary.dynamics.series,
                        summary.dynamics.granularity,
                        caption(SOURCE_ACTIONS),
                        compact,
                      )
                    : null
                }
              />
            </div>
            <div className="col-span-12 min-w-0">
              <ChartCard
                chartId="kam-activity-by-kam"
                title="Взаимодействия по КАМам: текущий и прошлый период"
                loading={pending}
                height={rowsHeight(byKam.length, 300)}
                empty={byKam.length === 0}
                option={
                  byKam.length > 0 && previous
                    ? kamCompareOption(
                        byKam,
                        caption(SOURCE_ACTIONS),
                        { current: 'Текущий период', previous: previousLabel },
                        compact,
                      )
                    : null
                }
                onSegmentClick={({ dataKey }) => {
                  if (dataKey) onSelectKam(dataKey);
                }}
              />
            </div>
          </div>

          <SectionCard title="Разрез по КАМам">
            <div className="max-w-full overflow-x-auto">
              <Table className="min-w-[760px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>КАМ</TableHead>
                    <TableHead className="text-right">Взаимодействия</TableHead>
                    <TableHead className="text-right">Прошлый период</TableHead>
                    <TableHead className="text-right">Вузы с контактом</TableHead>
                    <TableHead className="text-right">Новых</TableHead>
                    <TableHead className="text-right">Выигр. / отказ</TableHead>
                    <TableHead className="text-right">Конверсия</TableHead>
                    <TableHead className="text-right">Зависших</TableHead>
                    <TableHead>Последний контакт</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {byKam.map((row) => (
                    <TableRow key={row.kam_id}>
                      <TableCell>
                        <button
                          type="button"
                          onClick={() => onSelectKam(row.kam_id)}
                          className="rounded-sm text-left font-medium underline-offset-2 outline-none hover:text-primary hover:underline focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {row.kam}
                        </button>
                        {!row.is_active ? (
                          <StatusBadge status="draft" size="sm" className="ml-1.5">
                            Неактивен
                          </StatusBadge>
                        ) : null}
                      </TableCell>
                      <TableCell className="tabular text-right font-semibold">
                        {row.interactions}
                      </TableCell>
                      <TableCell className="tabular text-right text-muted-foreground">
                        {row.interactions_prev}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {row.universities_assigned > 0
                          ? `${row.assigned_touched} из ${row.universities_assigned}`
                          : row.universities_touched}
                      </TableCell>
                      <TableCell className="tabular text-right">{row.new_requests}</TableCell>
                      <TableCell className="tabular text-right">
                        {row.won} / {row.lost}
                      </TableCell>
                      <TableCell className="tabular text-right">
                        {formatPercent(row.conversion)}
                      </TableCell>
                      <TableCell className="tabular text-right">{row.stuck || '—'}</TableCell>
                      <TableCell className="tabular">{formatDay(row.last_contact_at)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </SectionCard>

          <SectionCard title="Где застревают: топ зависших заявок">
            {(summary?.top_stuck.length ?? 0) === 0 ? (
              <p className="px-2 pb-1 text-sm text-muted-foreground">
                Зависших заявок на конец периода нет.
              </p>
            ) : (
              <div className="max-w-full overflow-x-auto">
                <Table className="min-w-[720px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead>Заявка</TableHead>
                      <TableHead>Вуз / клиент</TableHead>
                      <TableHead>КАМ</TableHead>
                      <TableHead>Этап</TableHead>
                      <TableHead className="text-right">Дней / порог</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {summary?.top_stuck.map((item) => (
                      <TableRow key={item.request_id}>
                        <TableCell>
                          <RequestLink request={item} onOpen={onOpenRequest} className="text-sm" />
                        </TableCell>
                        <TableCell>{item.university ?? '—'}</TableCell>
                        <TableCell>{item.kam}</TableCell>
                        <TableCell>
                          <StatusBadge color={item.stage.color ?? undefined} size="sm">
                            {item.stage.name}
                          </StatusBadge>
                        </TableCell>
                        <TableCell className="tabular text-right font-medium text-status-warning-deep">
                          {item.days_on_stage} / {item.threshold_days}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </SectionCard>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}
