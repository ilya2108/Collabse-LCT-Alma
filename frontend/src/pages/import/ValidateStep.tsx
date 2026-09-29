import {
  ArrowLeft,
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  CloudUpload,
  Download,
  FileSpreadsheet,
  Loader2,
  RotateCw,
  TriangleAlert,
  XCircle,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { useOnboarding } from '@/features/onboarding';
import { downloadFile } from '@/shared/api/endpoints/files';
import type {
  ImportSession,
  ImportUpdateStrategy,
  ImportValidationReport,
} from '@/shared/api/types';
import { Illustration } from '@/shared/illustrations';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { toastError } from '@/shared/lib/toast';
import { Alert, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Progress } from '@/shared/ui/progress';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import { Skeleton } from '@/shared/ui/skeleton';
import { Switch } from '@/shared/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { registryPathFor } from './importModel';

/**
 * Шаг 4 мастера импорта (redesign.md §6.6): сводка 4 StatCard-мини
 * (всего/пройдут/предупреждения/ошибки, тона draft/success/warning/danger),
 * построчный отчёт с фильтром «только ошибки» (Switch), radio политики дублей,
 * применение с Progress и финальный success-экран со счётчиками и 3 кнопками.
 * Строки с ошибками никогда не блокируют импорт корректных.
 */

const ERRORS_PAGE_SIZE = 10;

interface ValidateStepProps {
  session: ImportSession;
  entity: string;
  report: ImportValidationReport | null;
  validating: boolean;
  onValidate: () => void;
  strategy: ImportUpdateStrategy;
  strategyUpdating: boolean;
  onStrategyChange: (strategy: ImportUpdateStrategy) => void;
  applying: boolean;
  onApply: () => void;
  onBack: () => void;
  onRestart: () => void;
}

async function downloadReport(fileId: string, name: string): Promise<void> {
  try {
    await downloadFile(fileId, name);
  } catch (error) {
    toastError(error, { title: 'Не удалось скачать отчёт' });
  }
}

/** StatCard-мини (§6.6): иконка в тонированном квадрате + число tabular. */
function MiniStatCard({
  icon: Icon,
  tone,
  title,
  value,
}: {
  icon: LucideIcon;
  tone: 'draft' | 'success' | 'warning' | 'danger';
  title: string;
  value: number;
}): ReactNode {
  return (
    <div className="flex min-w-[160px] flex-1 items-center gap-3 rounded-lg border bg-card p-3 shadow-card">
      <span
        className={cn(
          'flex size-10 shrink-0 items-center justify-center rounded-md',
          tone === 'draft' && 'bg-status-draft-tint text-status-draft-deep',
          tone === 'success' && 'bg-status-success-tint text-status-success-deep',
          tone === 'warning' && 'bg-status-warning-tint text-status-warning-deep',
          tone === 'danger' && 'bg-status-danger-tint text-status-danger-deep',
        )}
        aria-hidden="true"
      >
        <Icon className="size-5" aria-hidden="true" />
      </span>
      <span className="min-w-0">
        <span className="tabular block text-xl font-semibold leading-7">{value}</span>
        <span className="block truncate text-xs text-muted-foreground">{title}</span>
      </span>
    </div>
  );
}

export function ValidateStep({
  session,
  entity,
  report,
  validating,
  onValidate,
  strategy,
  strategyUpdating,
  onStrategyChange,
  applying,
  onApply,
  onBack,
  onRestart,
}: ValidateStepProps): ReactNode {
  const [onlyErrors, setOnlyErrors] = useState(false);
  const [errorsPage, setErrorsPage] = useState(0);
  const reduced = useReducedMotion() ?? false;
  // Чек-лист онбординга §7.5: импорт выполнен (no-op без провайдера).
  const { completeChecklistItem } = useOnboarding();
  const completed = session.state === 'completed';
  useEffect(() => {
    if (completed) completeChecklistItem('run-import');
  }, [completed, completeChecklistItem]);

  const visibleErrors = useMemo(() => {
    if (!report) return [];
    return onlyErrors
      ? report.errors.filter((e) => (e.level ?? 'error') === 'error')
      : report.errors;
  }, [report, onlyErrors]);

  // --- Импорт завершён -------------------------------------------------------
  if (session.state === 'completed' && session.result) {
    const { created, updated, skipped, failed, report_file_id } = session.result;
    return (
      <div className="flex flex-col items-center py-10 text-center">
        <motion.div
          initial={reduced ? { opacity: 0 } : { opacity: 0, scale: 0.7 }}
          animate={reduced ? { opacity: 1 } : { opacity: 1, scale: 1 }}
          transition={springs.bouncy}
          aria-hidden="true"
        >
          <Illustration name="import-done" height={160} />
        </motion.div>
        {failed > 0 ? (
          <p className="mt-3 inline-flex items-center gap-1.5 rounded-sm bg-status-warning-tint px-2 py-0.5 text-xs font-medium text-status-warning-deep">
            <TriangleAlert className="size-3.5" aria-hidden="true" />
            Часть строк с ошибками
          </p>
        ) : null}
        <p className="tabular mt-4 text-[28px] font-semibold leading-9">
          Импортировано: {created + updated}
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Создано {created} · обновлено {updated} · пропущено {skipped}
          {failed > 0 ? ` · с ошибками ${failed}` : ''}
        </p>
        <div className="mt-6 flex flex-wrap justify-center gap-2">
          <Button asChild>
            <Link to={registryPathFor(entity)}>Открыть реестр</Link>
          </Button>
          {report_file_id ? (
            <Button
              variant="outline"
              onClick={() => void downloadReport(report_file_id, 'import-report.xlsx')}
            >
              <Download aria-hidden="true" /> Скачать отчёт
            </Button>
          ) : null}
          <Button variant="outline" onClick={onRestart}>
            Новый импорт
          </Button>
        </div>
      </div>
    );
  }

  // --- Применение в процессе ---------------------------------------------------
  if (session.state === 'applying') {
    const progress = session.progress;
    const percent =
      progress && progress.total > 0
        ? Math.round((progress.done / progress.total) * 100)
        : null;
    return (
      <div className="mx-auto max-w-md space-y-3 py-10 text-center">
        <p className="text-base font-semibold">Импортируем строки…</p>
        <Progress value={percent ?? 0} aria-label="Прогресс импорта" />
        {progress ? (
          <p className="tabular text-sm text-muted-foreground" aria-live="polite">
            {progress.done} из {progress.total}
          </p>
        ) : null}
      </div>
    );
  }

  // --- Валидация ----------------------------------------------------------------
  if (!report) {
    return (
      <div className="space-y-4 py-8 text-center">
        {validating ? (
          <>
            <div className="mx-auto max-w-md space-y-2">
              <Skeleton className="h-4" />
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-4 w-1/2" />
            </div>
            <p className="text-sm text-muted-foreground" aria-live="polite">
              Проверяем строки без записи в базу…
            </p>
          </>
        ) : (
          <>
            <p className="text-sm">Проверка ещё не запускалась</p>
            <div className="flex justify-center gap-2">
              <Button variant="outline" onClick={onBack}>
                <ArrowLeft aria-hidden="true" /> Назад к маппингу
              </Button>
              <Button onClick={onValidate}>Запустить проверку</Button>
            </div>
          </>
        )}
      </div>
    );
  }

  const warningsCount = report.errors.filter((e) => e.level === 'warning').length;
  const totalPages = Math.max(1, Math.ceil(visibleErrors.length / ERRORS_PAGE_SIZE));
  const page = Math.min(errorsPage, totalPages - 1);
  const pageErrors = visibleErrors.slice(
    page * ERRORS_PAGE_SIZE,
    (page + 1) * ERRORS_PAGE_SIZE,
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3">
        <MiniStatCard icon={FileSpreadsheet} tone="draft" title="Всего строк" value={report.total_rows} />
        <MiniStatCard icon={CircleCheck} tone="success" title="Пройдут импорт" value={report.valid_rows} />
        <MiniStatCard icon={TriangleAlert} tone="warning" title="Предупреждения" value={warningsCount} />
        <MiniStatCard icon={XCircle} tone="danger" title="С ошибками" value={report.error_rows} />
      </div>

      {report.errors.length > 0 ? (
        <>
          <div className="flex flex-wrap items-center gap-4">
            {warningsCount > 0 ? (
              <label className="flex items-center gap-2 text-sm">
                <Switch
                  checked={onlyErrors}
                  onCheckedChange={(checked) => {
                    setOnlyErrors(checked);
                    setErrorsPage(0);
                  }}
                />
                Только ошибки
              </label>
            ) : null}
            {report.errors_truncated ? (
              <span className="text-sm text-muted-foreground">
                Показаны первые {report.errors.length} проблем — полный список в XLSX-отчёте
              </span>
            ) : null}
            {report.error_report_file_id ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  void downloadReport(
                    report.error_report_file_id as string,
                    'import-errors.xlsx',
                  )
                }
              >
                <Download aria-hidden="true" /> Скачать отчёт XLSX
              </Button>
            ) : null}
          </div>
          <div className="overflow-hidden rounded-md border">
            <Table>
              <TableHeader className="bg-muted">
                <TableRow>
                  <TableHead scope="col" className="w-[90px] text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    № строки
                  </TableHead>
                  <TableHead scope="col" className="w-[150px] text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Колонка
                  </TableHead>
                  <TableHead scope="col" className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Проблема
                  </TableHead>
                  <TableHead scope="col" className="w-[150px] text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    Уровень
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pageErrors.map((record) => (
                  <TableRow key={`${record.row}-${record.column ?? ''}-${record.code}`}>
                    <TableCell className="tabular">{record.row}</TableCell>
                    <TableCell className="max-w-[150px] truncate" title={record.column ?? undefined}>
                      {record.column ?? '—'}
                    </TableCell>
                    <TableCell>{record.message}</TableCell>
                    <TableCell>
                      {(record.level ?? 'error') === 'error' ? (
                        <span className="inline-flex items-center gap-1 rounded-sm bg-status-danger-tint px-1.5 py-0.5 text-xs font-medium text-status-danger-deep">
                          <XCircle className="size-3" aria-hidden="true" /> ошибка
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-sm bg-status-warning-tint px-1.5 py-0.5 text-xs font-medium text-status-warning-deep">
                          <TriangleAlert className="size-3" aria-hidden="true" /> предупреждение
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {totalPages > 1 ? (
            <div className="flex items-center justify-end gap-2 text-sm text-muted-foreground">
              <span className="tabular">
                {page * ERRORS_PAGE_SIZE + 1}–
                {Math.min((page + 1) * ERRORS_PAGE_SIZE, visibleErrors.length)} из{' '}
                {visibleErrors.length}
              </span>
              <Button
                variant="outline"
                size="icon"
                aria-label="Предыдущая страница"
                disabled={page === 0}
                onClick={() => setErrorsPage(page - 1)}
              >
                <ChevronLeft aria-hidden="true" />
              </Button>
              <Button
                variant="outline"
                size="icon"
                aria-label="Следующая страница"
                disabled={page >= totalPages - 1}
                onClick={() => setErrorsPage(page + 1)}
              >
                <ChevronRight aria-hidden="true" />
              </Button>
            </div>
          ) : null}
        </>
      ) : (
        <Alert variant="success">
          <CircleCheck aria-hidden="true" />
          <AlertTitle>Все строки прошли проверку</AlertTitle>
        </Alert>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Дубликаты:</span>
        <RadioGroup
          value={strategy}
          disabled={strategyUpdating}
          onValueChange={(value) => onStrategyChange(value as ImportUpdateStrategy)}
          className="flex flex-wrap gap-4"
        >
          <label className="flex items-center gap-2 text-sm">
            <RadioGroupItem value="upsert" /> Обновлять существующие
          </label>
          <label className="flex items-center gap-2 text-sm">
            <RadioGroupItem value="create_only" /> Пропускать (только новые)
          </label>
        </RadioGroup>
        {strategyUpdating ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden="true" />
        ) : null}
      </div>

      {report.error_rows > 0 ? (
        <p className="text-sm text-muted-foreground">
          Строки с ошибками будут пропущены — они не блокируют импорт корректных. Отчёт можно
          поправить в Excel и загрузить заново.
        </p>
      ) : null}

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft aria-hidden="true" /> Назад к маппингу
        </Button>
        <Button variant="outline" disabled={validating} onClick={onValidate}>
          {validating ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <RotateCw aria-hidden="true" />
          )}
          Проверить заново
        </Button>
        <Button disabled={report.valid_rows === 0 || applying} onClick={onApply}>
          {applying ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <CloudUpload aria-hidden="true" />
          )}
          Импортировать {report.valid_rows} корректных строк
        </Button>
      </div>
    </div>
  );
}
