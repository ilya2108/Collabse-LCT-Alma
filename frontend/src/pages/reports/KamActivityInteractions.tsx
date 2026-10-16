import {
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Download,
  ExternalLink,
  Flame,
  LoaderCircle,
} from 'lucide-react';
import { useEffect, useMemo, useState, type MouseEvent, type ReactNode } from 'react';
import type {
  InteractionsColumn,
  InteractionsColumnKey,
  InteractionsExportFormat,
  InteractionsReport,
  InteractionsRow,
} from '@/shared/api/endpoints/kamActivity';
import { cn } from '@/shared/lib/cn';
import { formatMoney } from '@/shared/lib/format';
import { Button } from '@/shared/ui/button';
import { StageTag, StatusBadge } from '@/shared/ui/data-table';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/shared/ui/table';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { OutcomeBadge } from './KamActivityParts';
import { formatDay, formatDays, plural } from './kamActivityModel';

/**
 * Вкладка «Взаимодействия за период» (ТЗ ФТ4): таблица заявок, с которыми шла работа
 * в периоде; набор колонок выбирает пользователь (вуз, направление и продукт, этап на
 * конец периода, ответственный, даты). Выбор колонок хранится в URL (`cols=`)
 * и уходит в выгрузку XLSX/PDF/CSV как есть: файл совпадает с экраном.
 */

const PAGE_SIZE = 50;
const GROUP_ORDER = ['ТЗ', 'Даты', 'Дополнительно'];
const NUMERIC: ReadonlySet<InteractionsColumnKey> = new Set(['days_on_stage', 'interactions', 'amount']);

function Empty({ hint }: { hint?: string }): ReactNode {
  const dash = <span className="text-muted-foreground">—</span>;
  if (!hint) return dash;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className="rounded-sm text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
          —
        </span>
      </TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}

function PlaceCell({ row }: { row: InteractionsRow }): ReactNode {
  if (row.university) {
    return <span className="font-medium">{row.university}</span>;
  }
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span>{row.client ?? '—'}</span>
      <StatusBadge status="draft" size="sm">
        B2C
      </StatusBadge>
    </span>
  );
}

function DaysCell({ row }: { row: InteractionsRow }): ReactNode {
  if (row.stage.is_terminal) return <Empty />;
  return (
    <span
      className={cn(
        'tabular inline-flex items-center justify-end gap-1',
        row.is_stuck && 'font-medium text-status-warning-deep',
      )}
    >
      {formatDays(row.days_on_stage)}
      {row.is_stuck ? (
        <Flame className="size-3.5" aria-label={`Зависла: порог ${row.threshold_days} дн.`} />
      ) : null}
    </span>
  );
}

function renderCell(key: InteractionsColumnKey, row: InteractionsRow): ReactNode {
  switch (key) {
    case 'university':
      return <PlaceCell row={row} />;
    case 'it_direction':
      return row.it_direction ?? <Empty hint="Появится вместе со справочником ИТ-направлений" />;
    case 'product':
      return row.product ?? <Empty />;
    case 'program':
      return row.program ?? <Empty />;
    case 'status':
      return (
        <span className="flex flex-wrap items-center gap-1.5">
          <StageTag size="sm" color={row.stage.color} name={row.stage.name} />
          <OutcomeBadge outcome={row.closed_in_period} />
        </span>
      );
    case 'responsible':
      return row.responsible;
    case 'title':
      return row.title;
    case 'workflow_type':
      return row.workflow_type.toUpperCase();
    case 'created_at':
      return <span className="tabular">{formatDay(row.created_at)}</span>;
    case 'stage_since':
      return <span className="tabular">{formatDay(row.stage_since)}</span>;
    case 'last_contact_at':
      return row.last_contact_at ? (
        <span className="tabular">{formatDay(row.last_contact_at)}</span>
      ) : (
        <Empty hint="Собственных действий КАМа по заявке не было" />
      );
    case 'days_on_stage':
      return <DaysCell row={row} />;
    case 'closed_in_period':
      return row.closed_in_period ? <OutcomeBadge outcome={row.closed_in_period} /> : <Empty />;
    case 'interactions':
      return (
        <Tooltip>
          <TooltipTrigger asChild>
            <span
              tabIndex={0}
              className={cn(
                'tabular rounded-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring',
                row.interactions.total === 0 && 'font-normal text-muted-foreground',
              )}
            >
              {row.interactions.total}
            </span>
          </TooltipTrigger>
          <TooltipContent>
            Переходы {row.interactions.transitions} · комментарии {row.interactions.comments} ·
            файлы {row.interactions.files}
          </TooltipContent>
        </Tooltip>
      );
    case 'amount':
      return <span className="tabular">{formatMoney(row.amount)}</span>;
    default:
      return null;
  }
}

interface ColumnPickerProps {
  columns: InteractionsColumn[];
  selected: InteractionsColumnKey[];
  onChange: (columns: InteractionsColumnKey[] | null) => void;
}

function ColumnPicker({ columns, selected, onChange }: ColumnPickerProps): ReactNode {
  const groups = GROUP_ORDER.map((group) => ({
    group,
    items: columns.filter((column) => column.group === group),
  })).filter((item) => item.items.length > 0);
  const toggle = (key: InteractionsColumnKey, checked: boolean): void => {
    const next = checked ? [...selected, key] : selected.filter((item) => item !== key);
    if (next.length === 0) return; // хотя бы одна колонка
    // порядок — как в каталоге отчёта (так же в файле)
    onChange(columns.map((column) => column.key).filter((item) => next.includes(item)));
  };
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">
          <Columns3 aria-hidden="true" />
          Колонки
          <span className="tabular text-xs text-muted-foreground">{selected.length}</span>
          <ChevronDown className="text-muted-foreground" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-[70vh] overflow-y-auto">
        {groups.map(({ group, items }, index) => (
          <div key={group}>
            {index > 0 ? <DropdownMenuSeparator /> : null}
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              {group === 'ТЗ' ? 'Колонки ТЗ' : group}
            </DropdownMenuLabel>
            {items.map((column) => (
              <DropdownMenuCheckboxItem
                key={column.key}
                checked={selected.includes(column.key)}
                disabled={selected.length === 1 && selected.includes(column.key)}
                onCheckedChange={(checked) => toggle(column.key, checked === true)}
                onSelect={(event) => event.preventDefault()}
              >
                {column.label}
              </DropdownMenuCheckboxItem>
            ))}
          </div>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => onChange(null)}>Колонки по умолчанию</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

interface InteractionsTabProps {
  report: InteractionsReport;
  columns: InteractionsColumnKey[];
  onColumnsChange: (columns: InteractionsColumnKey[] | null) => void;
  onOpenRequest: (id: string) => void;
  onExport: (format: InteractionsExportFormat) => void;
  exporting: InteractionsExportFormat | null;
}

export function KamActivityInteractions({
  report,
  columns,
  onColumnsChange,
  onOpenRequest,
  onExport,
  exporting,
}: InteractionsTabProps): ReactNode {
  const [page, setPage] = useState(0);
  const rows = report.rows;
  const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  useEffect(() => setPage(0), [report]);
  const visible = useMemo(
    () => rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE),
    [rows, page],
  );
  const labels = useMemo(
    () => new Map(report.columns.map((column) => [column.key, column.label])),
    [report.columns],
  );
  const totals = report.totals;

  const openRow = (event: MouseEvent<HTMLAnchorElement>, id: string): void => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) {
      return;
    }
    event.preventDefault();
    onOpenRequest(id);
  };

  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          <span className="tabular font-medium text-foreground">{totals.requests}</span>{' '}
          {plural(totals.requests, ['заявка', 'заявки', 'заявок'])} · вузов{' '}
          <span className="tabular">{totals.universities}</span> · с действиями КАМа{' '}
          <span className="tabular">{totals.with_interactions}</span> · зависших{' '}
          <span className="tabular">{totals.stuck}</span>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <ColumnPicker columns={report.columns} selected={columns} onChange={onColumnsChange} />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button disabled={exporting !== null}>
                {exporting ? (
                  <LoaderCircle className="animate-spin" aria-hidden="true" />
                ) : (
                  <Download aria-hidden="true" />
                )}
                Выгрузить
                <ChevronDown aria-hidden="true" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel className="text-xs text-muted-foreground">
                Выбранные колонки: {columns.length}
              </DropdownMenuLabel>
              <DropdownMenuItem onSelect={() => onExport('xlsx')}>XLSX</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onExport('pdf')}>PDF</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => onExport('csv')}>CSV (ru-Excel, «;»)</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>

      {report.truncated ? (
        <p className="rounded-md bg-status-warning-tint px-3 py-2 text-sm text-status-warning-deep">
          Показаны первые {rows.length} строк — полный список в выгрузке.
        </p>
      ) : null}

      <div className="max-w-full overflow-x-auto rounded-lg border border-border bg-card">
        <Table className="min-w-[760px]">
          <TableHeader>
            <TableRow>
              {columns.map((key) => (
                <TableHead key={key} className={cn(NUMERIC.has(key) && 'text-right')}>
                  {labels.get(key) ?? key}
                </TableHead>
              ))}
              <TableHead className="w-10">
                <span className="sr-only">Карточка заявки</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visible.map((row) => (
              <TableRow key={row.request_id}>
                {columns.map((key) => (
                  <TableCell
                    key={key}
                    className={cn('align-top text-sm', NUMERIC.has(key) && 'text-right')}
                  >
                    {renderCell(key, row)}
                  </TableCell>
                ))}
                <TableCell className="align-top">
                  <a
                    href={`/requests/${row.request_id}`}
                    onClick={(event) => openRow(event, row.request_id)}
                    className="inline-flex rounded-sm p-1 text-muted-foreground outline-none hover:text-primary focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={`Открыть заявку «${row.title}»`}
                    title={row.title}
                  >
                    <ExternalLink className="size-4" aria-hidden="true" />
                  </a>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {pages > 1 ? (
        <div className="flex items-center justify-end gap-2 text-sm text-muted-foreground">
          <span className="tabular">
            {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, rows.length)} из {rows.length}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((value) => Math.max(0, value - 1))}
            disabled={page === 0}
            aria-label="Предыдущая страница"
          >
            <ChevronLeft aria-hidden="true" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((value) => Math.min(pages - 1, value + 1))}
            disabled={page >= pages - 1}
            aria-label="Следующая страница"
          >
            <ChevronRight aria-hidden="true" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
