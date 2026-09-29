import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  applyImportSession,
  createImportSession,
  deleteImportSession,
  getImportSession,
  putImportMapping,
  validateImportSession,
} from '@/shared/api/endpoints/importSessions';
import type {
  ImportMappingEntry,
  ImportMappingOptions,
  ImportSession,
  ImportUpdateStrategy,
  ImportValidationReport,
} from '@/shared/api/types';
import { toastError } from '@/shared/lib/toast';
import {
  LocalErrorState,
  LocalLoadingState,
  LocalPageHeader,
} from '@/components/wp5/PageChrome';
import { Button } from '@/shared/ui/button';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import { Stepper } from '@/shared/ui/stepper';
import {
  ENTITY_OPTIONS,
  buildPreview,
  fileKind,
  isDictionaryEntity,
  looksMojibake,
  type FileKind,
} from './importModel';
import { MappingStep } from './MappingStep';
import { PreviewStep } from './PreviewStep';
import { UploadStep } from './UploadStep';
import { ValidateStep } from './ValidateStep';

/**
 * Мастер импорта (redesign.md §6.6): 4 шага на Stepper §3.10 — файл → превью
 * и кодировка → интерактивный маппинг → валидация и применение. Сессия живёт
 * на сервере (id — в URL), поэтому F5 не убивает прогресс; исходный файл
 * дополнительно держим в памяти браузера для мгновенного превью смены
 * кодировки CSV.
 */

const WIZARD_STEPS = [
  { id: 'file', title: 'Файл' },
  { id: 'preview', title: 'Превью и кодировка' },
  { id: 'mapping', title: 'Маппинг колонок' },
  { id: 'validate', title: 'Валидация и импорт' },
];

interface WizardOptions {
  sheet: number;
  headerRow: boolean;
  encoding: string | null;
  strategy: ImportUpdateStrategy;
}

const DEFAULT_OPTIONS: WizardOptions = {
  sheet: 0,
  headerRow: true,
  encoding: null,
  strategy: 'upsert',
};

function serverOptions(options: WizardOptions): ImportMappingOptions {
  return {
    sheet: options.sheet,
    header_row: options.headerRow ? 0 : null,
    encoding: options.encoding,
    update_strategy: options.strategy,
    skip_empty_rows: true,
  };
}

function sanitizeMapping(mapping: ImportMappingEntry[]): ImportMappingEntry[] {
  return mapping
    .filter((entry) => entry.target_field && entry.source_column >= 0)
    .map((entry) => ({
      ...entry,
      transform: entry.transform || undefined,
    }));
}

function optionsFromSession(session: ImportSession): WizardOptions {
  return {
    sheet: session.options?.sheet ?? session.detected?.active_sheet ?? 0,
    headerRow: (session.options?.header_row ?? session.detected?.header_row ?? 0) !== null,
    encoding: session.options?.encoding ?? null,
    strategy: session.options?.update_strategy ?? 'upsert',
  };
}

function mappingFromSession(session: ImportSession): ImportMappingEntry[] {
  if (session.mapping && session.mapping.length > 0) return session.mapping;
  return (session.suggested_mapping ?? []).map((s) => ({
    source_column: s.source_column,
    target_field: s.target_field,
  }));
}

export function ImportWizardPage(): ReactNode {
  const [searchParams, setSearchParams] = useSearchParams();

  const entityParam = searchParams.get('entity');
  const [entity, setEntity] = useState<string>(
    entityParam &&
      (ENTITY_OPTIONS.some((o) => o.value === entityParam) || isDictionaryEntity(entityParam))
      ? entityParam
      : 'universities',
  );
  const [step, setStep] = useState(0);
  const [session, setSession] = useState<ImportSession | null>(null);
  const [buffer, setBuffer] = useState<ArrayBuffer | null>(null);
  const [clientKind, setClientKind] = useState<FileKind | null>(null);
  const [mapping, setMapping] = useState<ImportMappingEntry[]>([]);
  const [options, setOptions] = useState<WizardOptions>(DEFAULT_OPTIONS);
  const [report, setReport] = useState<ImportValidationReport | null>(null);
  const [cancelOpen, setCancelOpen] = useState(false);

  const sessionParam = searchParams.get('session');

  const resetWizard = (): void => {
    setSession(null);
    setBuffer(null);
    setClientKind(null);
    setMapping([]);
    setOptions(DEFAULT_OPTIONS);
    setReport(null);
    setStep(0);
    setCancelOpen(false);
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete('session');
        return next;
      },
      { replace: true },
    );
  };

  // --- Восстановление сессии после F5 (ux.md §11: состояние на сервере) -------
  const resumeQuery = useQuery({
    queryKey: ['import-session-resume', sessionParam],
    queryFn: ({ signal }) => getImportSession(sessionParam as string, signal),
    enabled: Boolean(sessionParam) && !session,
    retry: 0,
    staleTime: Infinity,
  });
  useEffect(() => {
    const restored = resumeQuery.data;
    if (!restored || session) return;
    setSession(restored);
    setEntity(restored.entity_type);
    setOptions(optionsFromSession(restored));
    setMapping(mappingFromSession(restored));
    setReport(restored.validation ?? null);
    if (
      restored.state === 'validated' ||
      restored.state === 'applying' ||
      restored.state === 'completed'
    ) {
      setStep(3);
    } else {
      setStep(restored.mapping && restored.mapping.length > 0 ? 2 : 1);
    }
  }, [resumeQuery.data, session]);

  // --- Шаг 1: загрузка файла ----------------------------------------------------
  const createMutation = useMutation({
    mutationFn: (file: File) => createImportSession(file, entity),
    meta: { silent: true },
    onSuccess: (created) => {
      setSession(created);
      setOptions(optionsFromSession(created));
      setMapping(mappingFromSession(created));
      setReport(null);
      setStep(1);
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set('session', created.id);
          return next;
        },
        { replace: true },
      );
    },
    onError: (error) => toastError(error, { title: 'Не удалось распознать файл' }),
  });

  const handleStart = (file: File): void => {
    setClientKind(fileKind(file.name));
    void file
      .arrayBuffer()
      .then((data) => setBuffer(data))
      .catch(() => setBuffer(null));
    createMutation.mutate(file);
  };

  // --- Синхронизация опций с сервером (лист/заголовок/кодировка) ----------------
  const refreshMutation = useMutation({
    mutationFn: ({ nextOptions }: { nextOptions: WizardOptions; resetMapping: boolean }) =>
      putImportMapping(session?.id as string, {
        mapping: sanitizeMapping(mapping),
        options: serverOptions(nextOptions),
      }),
    meta: { silent: true },
    onSuccess: (updated, { resetMapping }) => {
      setSession((prev) => (prev ? { ...prev, ...updated } : updated));
      if (resetMapping) setMapping(mappingFromSession(updated));
    },
    onError: (error) => {
      toastError(error, {
        title:
          'Сервер не смог перечитать файл с новыми параметрами — проверьте колонки на шаге маппинга',
      });
    },
  });

  const changeOptions = (patch: Partial<WizardOptions>, resetMapping = false): void => {
    const nextOptions = { ...options, ...patch };
    setOptions(nextOptions);
    if (session) refreshMutation.mutate({ nextOptions, resetMapping });
  };

  // --- Шаг 3→4: сохранить маппинг и провалидировать ------------------------------
  const validateMutation = useMutation({
    mutationFn: () => validateImportSession(session?.id as string),
    meta: { silent: true },
    onSuccess: (result) => {
      setReport(result);
      setSession((prev) => (prev ? { ...prev, state: result.state || prev.state } : prev));
    },
    onError: (error) => toastError(error, { title: 'Проверка данных не выполнена' }),
  });

  const saveMappingMutation = useMutation({
    mutationFn: () =>
      putImportMapping(session?.id as string, {
        mapping: sanitizeMapping(mapping),
        options: serverOptions(options),
      }),
    meta: { silent: true },
    onSuccess: (updated) => {
      setSession((prev) => (prev ? { ...prev, ...updated } : updated));
      setReport(null);
      setStep(3);
      validateMutation.mutate();
    },
    onError: (error) => toastError(error, { title: 'Не удалось сохранить маппинг' }),
  });

  const strategyMutation = useMutation({
    mutationFn: (nextStrategy: ImportUpdateStrategy) =>
      putImportMapping(session?.id as string, {
        mapping: sanitizeMapping(mapping),
        options: serverOptions({ ...options, strategy: nextStrategy }),
      }),
    meta: { silent: true },
    onSuccess: () => validateMutation.mutate(),
    onError: (error) => toastError(error, { title: 'Не удалось изменить политику дублей' }),
  });

  // --- Применение и поллинг прогресса ---------------------------------------------
  const applyMutation = useMutation({
    mutationFn: () => applyImportSession(session?.id as string, { mode: 'skip_errors' }),
    meta: { silent: true },
    onSuccess: (updated) => setSession((prev) => (prev ? { ...prev, ...updated } : updated)),
    onError: (error) => toastError(error, { title: 'Импорт не выполнен' }),
  });

  const pollQuery = useQuery({
    queryKey: ['import-session-poll', session?.id],
    queryFn: ({ signal }) => getImportSession(session?.id as string, signal),
    enabled: session?.state === 'applying',
    refetchInterval: 2000,
  });
  useEffect(() => {
    const polled = pollQuery.data;
    if (polled) {
      setSession((prev) => (prev && prev.id === polled.id ? { ...prev, ...polled } : prev));
    }
  }, [pollQuery.data]);

  // --- Отмена сессии -----------------------------------------------------------------
  const cancelMutation = useMutation({
    mutationFn: () => deleteImportSession(session?.id as string),
    meta: { silent: true },
    onSettled: () => resetWizard(),
  });

  // --- Превью (клиентское для CSV/JSON, серверное для Excel и после F5) --------------
  const preview = useMemo(
    () =>
      session
        ? buildPreview(session, buffer, clientKind, options.encoding, options.headerRow)
        : null,
    [session, buffer, clientKind, options.encoding, options.headerRow],
  );
  const mojibake = useMemo(() => looksMojibake(preview), [preview]);

  // --- Рендер -------------------------------------------------------------------------
  let content: ReactNode;
  if (sessionParam && !session) {
    content = resumeQuery.isError ? (
      <LocalErrorState
        error={resumeQuery.error}
        title="Сессия импорта не найдена или истекла"
        onRetry={resetWizard}
      />
    ) : (
      <LocalLoadingState rows={6} />
    );
  } else if (step === 0 || !session) {
    content = (
      <UploadStep
        entity={entity}
        onEntityChange={setEntity}
        uploading={createMutation.isPending}
        onStart={handleStart}
      />
    );
  } else if (step === 1) {
    content = (
      <PreviewStep
        session={session}
        preview={preview}
        mojibake={mojibake}
        isCsv={(clientKind ?? (session.file?.format === 'csv' ? 'csv' : 'excel')) === 'csv'}
        sheet={options.sheet}
        headerRow={options.headerRow}
        encoding={options.encoding}
        refreshing={refreshMutation.isPending}
        onChangeSheet={(sheet) => changeOptions({ sheet }, true)}
        onChangeHeaderRow={(headerRow) => changeOptions({ headerRow })}
        onChangeEncoding={(encoding) => changeOptions({ encoding })}
        onBack={() => cancelMutation.mutate()}
        onNext={() => setStep(2)}
      />
    );
  } else if (step === 2) {
    content = (
      <MappingStep
        session={session}
        entity={entity}
        mapping={mapping}
        onChange={setMapping}
        saving={saveMappingMutation.isPending}
        onBack={() => setStep(1)}
        onNext={() => saveMappingMutation.mutate()}
      />
    );
  } else {
    content = (
      <ValidateStep
        session={session}
        entity={entity}
        report={report}
        validating={validateMutation.isPending}
        onValidate={() => validateMutation.mutate()}
        strategy={options.strategy}
        strategyUpdating={strategyMutation.isPending}
        onStrategyChange={(strategy) => {
          setOptions((prev) => ({ ...prev, strategy }));
          strategyMutation.mutate(strategy);
        }}
        applying={applyMutation.isPending}
        onApply={() => applyMutation.mutate()}
        onBack={() => setStep(2)}
        onRestart={resetWizard}
      />
    );
  }

  return (
    <>
      <LocalPageHeader
        title="Мастер импорта"
        subtitle="XLSX, XLS, CSV и JSON — с исправлением кодировок, интерактивным маппингом и построчным отчётом об ошибках"
        extra={
          session && session.state !== 'completed' ? (
            <Button
              variant="destructive"
              disabled={cancelMutation.isPending}
              onClick={() => setCancelOpen(true)}
            >
              Отменить импорт
            </Button>
          ) : undefined
        }
      />
      <div className="mb-3 rounded-lg border bg-card px-6 py-4 shadow-card">
        <Stepper steps={WIZARD_STEPS} current={step} />
      </div>
      <div className="rounded-lg border bg-card p-5 shadow-card">{content}</div>

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        tone="danger"
        title="Отменить импорт?"
        facts={[{ label: 'сессия импорта будет удалена, загруженный файл — отброшен' }]}
        confirmLabel="Отменить импорт"
        loading={cancelMutation.isPending}
        onConfirm={() => cancelMutation.mutate()}
      />
    </>
  );
}
