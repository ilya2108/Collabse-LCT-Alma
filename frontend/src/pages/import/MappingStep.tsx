import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronsUpDown,
  Loader2,
  Save,
  ShieldCheck,
  TriangleAlert,
  X,
} from 'lucide-react';
import { useId, useState, type ReactNode } from 'react';
import { createPreset, listPresets } from '@/shared/api/endpoints/presets';
import type { ImportMappingEntry, ImportSession, UiPreset } from '@/shared/api/types';
import { cn } from '@/shared/lib/cn';
import { toastSuccess } from '@/shared/lib/toast';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/shared/ui/command';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { TRANSFORM_OPTIONS, isPiiField, lookupOptionsFor } from './importModel';

/**
 * Шаг 3 мастера импорта (redesign.md §6.6): интерактивный маппинг карточками-
 * соответствиями со стрелками — поле CRM (звёздочка обязательности) →
 * Combobox колонки файла (пример значения в опции) → живой пример.
 * Авто-смапленные помечены бейджем «авто», неиспользуемые колонки —
 * в Collapsible; шаблоны маппинга хранятся в /ui/presets (screen: import:{entity}).
 */

interface MappingStepProps {
  session: ImportSession;
  entity: string;
  mapping: ImportMappingEntry[];
  onChange: (mapping: ImportMappingEntry[]) => void;
  saving: boolean;
  onBack: () => void;
  onNext: () => void;
}

interface ColumnInfo {
  index: number;
  name: string;
  samples: string[];
}

/** Combobox (Popover + Command, §5.1) выбора колонки файла с примером значения. */
function ColumnCombobox({
  columns,
  value,
  onSelect,
  onClear,
  label,
}: {
  columns: ColumnInfo[];
  value: number | undefined;
  onSelect: (index: number) => void;
  onClear: () => void;
  label: string;
}): ReactNode {
  const [open, setOpen] = useState(false);
  const current = columns.find((c) => c.index === value);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          aria-label={label}
          className={cn('w-[260px] justify-between font-normal', !current && 'text-muted-foreground')}
        >
          <span className="truncate">{current ? `«${current.name}»` : 'Колонка файла…'}</span>
          <ChevronsUpDown className="size-4 shrink-0 opacity-50" aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[320px] p-0" align="start">
        <Command>
          <CommandInput placeholder="Найти колонку…" />
          <CommandList>
            <CommandEmpty>Колонка не найдена</CommandEmpty>
            <CommandGroup>
              {current ? (
                <CommandItem
                  value="__clear__"
                  onSelect={() => {
                    onClear();
                    setOpen(false);
                  }}
                >
                  <X className="size-4" aria-hidden="true" />
                  Не импортировать это поле
                </CommandItem>
              ) : null}
              {columns.map((column) => (
                <CommandItem
                  key={column.index}
                  value={`${column.name} ${column.index}`}
                  onSelect={() => {
                    onSelect(column.index);
                    setOpen(false);
                  }}
                >
                  <Check
                    className={cn('size-4', column.index === value ? 'opacity-100' : 'opacity-0')}
                    aria-hidden="true"
                  />
                  <span className="min-w-0 flex-1 truncate">«{column.name}»</span>
                  {column.samples[0] ? (
                    <span className="max-w-[120px] truncate text-xs text-muted-foreground">
                      напр.: {column.samples[0]}
                    </span>
                  ) : null}
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}

export function MappingStep({
  session,
  entity,
  mapping,
  onChange,
  saving,
  onBack,
  onNext,
}: MappingStepProps): ReactNode {
  const queryClient = useQueryClient();
  const templateNameId = useId();
  const targetFields = session.target_fields ?? [];
  const columns: ColumnInfo[] = session.detected?.columns ?? [];
  const suggested = session.suggested_mapping ?? [];

  const presetScreen = `import:${entity}`;
  const presetsQuery = useQuery({
    queryKey: ['ui-presets', presetScreen],
    queryFn: ({ signal }) => listPresets(presetScreen, signal),
    staleTime: 60_000,
    retry: 0,
  });
  const [saveOpen, setSaveOpen] = useState(false);
  const [templateName, setTemplateName] = useState('');
  const savePresetMutation = useMutation({
    mutationFn: () =>
      createPreset({
        screen: presetScreen,
        name: templateName.trim(),
        is_default: false,
        state: { filters: { mapping } },
      }),
    onSuccess: () => {
      setSaveOpen(false);
      setTemplateName('');
      toastSuccess('Шаблон маппинга сохранён');
      void queryClient.invalidateQueries({ queryKey: ['ui-presets', presetScreen] });
    },
  });

  const applyTemplate = (preset: UiPreset): void => {
    const stored = (preset.state.filters as { mapping?: ImportMappingEntry[] } | undefined)
      ?.mapping;
    if (Array.isArray(stored)) {
      // применяем только строки, чьи колонки есть в текущем файле
      onChange(stored.filter((entry) => columns.some((c) => c.index === entry.source_column)));
    }
  };

  const entryFor = (field: string): ImportMappingEntry | undefined =>
    mapping.find((m) => m.target_field === field);

  const setEntry = (field: string, patch: Partial<ImportMappingEntry> | null): void => {
    if (patch === null) {
      onChange(mapping.filter((m) => m.target_field !== field));
      return;
    }
    const existing = entryFor(field);
    if (existing) {
      onChange(mapping.map((m) => (m.target_field === field ? { ...m, ...patch } : m)));
    } else if (patch.source_column !== undefined) {
      onChange([...mapping, { target_field: field, source_column: patch.source_column, ...patch }]);
    }
  };

  const isAuto = (entry: ImportMappingEntry | undefined): boolean =>
    Boolean(
      entry &&
        suggested.some(
          (s) => s.target_field === entry.target_field && s.source_column === entry.source_column,
        ),
    );

  const sampleFor = (entry: ImportMappingEntry | undefined): string => {
    if (!entry) return '';
    const column = columns.find((c) => c.index === entry.source_column);
    const raw = column?.samples[0] ?? '';
    switch (entry.transform) {
      case 'trim':
        return raw.trim();
      case 'lowercase':
        return raw.toLowerCase();
      case 'uppercase':
        return raw.toUpperCase();
      default:
        return raw;
    }
  };

  const usedColumns = new Set(mapping.map((m) => m.source_column));
  const unusedColumns = columns.filter((c) => !usedColumns.has(c.index));
  const missingRequired = targetFields.filter((f) => f.required && !entryFor(f.field));

  if (targetFields.length === 0) {
    return (
      <Alert variant="warning">
        <TriangleAlert aria-hidden="true" />
        <AlertTitle>Сервер не вернул целевые поля для маппинга</AlertTitle>
        <AlertDescription>
          <p>Продолжить нельзя — попробуйте пересоздать сессию импорта.</p>
          <Button variant="outline" size="sm" className="mt-2" onClick={onBack}>
            Загрузить файл заново
          </Button>
        </AlertDescription>
      </Alert>
    );
  }

  const nextButton = (
    <Button disabled={missingRequired.length > 0 || saving} onClick={onNext}>
      {saving ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
      Проверить данные <ArrowRight aria-hidden="true" />
    </Button>
  );

  return (
    <div className="space-y-3">
      {!presetsQuery.isError && (presetsQuery.data?.items.length ?? 0) > 0 ? (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">Шаблон маппинга:</span>
          <Select
            onValueChange={(id) => {
              const preset = presetsQuery.data?.items.find((p) => p.id === id);
              if (preset) applyTemplate(preset);
            }}
          >
            <SelectTrigger className="w-[240px]" aria-label="Шаблон маппинга">
              <SelectValue placeholder="Выбрать сохранённый…" />
            </SelectTrigger>
            <SelectContent>
              {(presetsQuery.data?.items ?? []).map((preset) => (
                <SelectItem key={preset.id} value={preset.id}>
                  {preset.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}

      <ul className="space-y-2">
        {targetFields.map((field) => {
          const entry = entryFor(field.field);
          const lookupOptions = lookupOptionsFor(field.field);
          const sample = sampleFor(entry);
          return (
            <li
              key={field.field}
              className={cn(
                'flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border px-3 py-2.5',
                field.required && !entry
                  ? 'border-status-warning bg-status-warning-tint/40'
                  : 'border-border bg-card',
              )}
            >
              <div className="flex w-[220px] shrink-0 flex-wrap items-center gap-1.5">
                <span className="text-sm font-medium">
                  {field.label}
                  {field.required ? (
                    <span className="text-status-danger-deep" aria-label="обязательное поле">
                      {' '}
                      *
                    </span>
                  ) : null}
                </span>
                {isPiiField(field.field) ? (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex items-center gap-1 rounded-sm bg-status-talent-tint px-1.5 py-0.5 text-xs font-medium text-status-talent-deep">
                        <ShieldCheck className="size-3" aria-hidden="true" />
                        ПДн
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>
                      Персональные данные будут зашифрованы на сервере (152-ФЗ)
                    </TooltipContent>
                  </Tooltip>
                ) : null}
              </div>
              <ArrowRight
                className={cn('size-4 shrink-0', entry ? 'text-primary' : 'text-border')}
                aria-hidden="true"
              />
              <ColumnCombobox
                columns={columns}
                value={entry?.source_column}
                label={`Колонка файла для поля «${field.label}»`}
                onSelect={(index) => setEntry(field.field, { source_column: index })}
                onClear={() => setEntry(field.field, null)}
              />
              {entry ? (
                <Select
                  value={entry.transform || '__none__'}
                  onValueChange={(value) =>
                    setEntry(field.field, { transform: value === '__none__' ? undefined : value })
                  }
                >
                  <SelectTrigger className="w-[190px]" size="sm" aria-label="Преобразование значения">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TRANSFORM_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value || '__none__'}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
              {entry && lookupOptions ? (
                <Select
                  value={entry.lookup ?? lookupOptions[0].value}
                  onValueChange={(value) => setEntry(field.field, { lookup: value })}
                >
                  <SelectTrigger className="w-[220px]" size="sm" aria-label="Правило поиска в справочнике">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {lookupOptions.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              ) : null}
              {isAuto(entry) ? (
                <span className="rounded-sm bg-primary-tint px-1.5 py-0.5 text-xs font-medium text-primary-active">
                  авто
                </span>
              ) : null}
              {entry ? (
                <span
                  className="max-w-[200px] truncate text-sm text-muted-foreground"
                  title={sample || undefined}
                >
                  → {sample || '—'}
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>

      {unusedColumns.length > 0 ? (
        <Collapsible>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="group text-muted-foreground">
              <ChevronDown
                className="size-4 transition-transform duration-150 group-data-[state=open]:rotate-180"
                aria-hidden="true"
              />
              Не используются ({unusedColumns.length})
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="flex flex-wrap gap-1.5 pt-2">
              {unusedColumns.map((column) => (
                <span
                  key={column.index}
                  className="rounded-sm bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                >
                  {column.name}
                </span>
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      ) : null}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft aria-hidden="true" /> Назад к превью
        </Button>
        {missingRequired.length > 0 ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">{nextButton}</span>
            </TooltipTrigger>
            <TooltipContent>
              Не смаплено: {missingRequired.map((f) => f.label).join(', ')}
            </TooltipContent>
          </Tooltip>
        ) : (
          nextButton
        )}
        <Button
          variant="outline"
          disabled={mapping.length === 0 || presetsQuery.isError}
          onClick={() => setSaveOpen(true)}
        >
          <Save aria-hidden="true" /> Сохранить шаблон маппинга
        </Button>
      </div>

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>Сохранить шаблон маппинга</DialogTitle>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor={templateNameId}>Название шаблона</Label>
            <Input
              id={templateNameId}
              value={templateName}
              onChange={(event) => setTemplateName(event.target.value)}
              placeholder="Например: Студенты из LMS-выгрузки…"
              maxLength={80}
              autoComplete="off"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setSaveOpen(false)}>
              Отмена
            </Button>
            <Button
              disabled={templateName.trim().length === 0 || savePresetMutation.isPending}
              onClick={() => savePresetMutation.mutate()}
            >
              {savePresetMutation.isPending ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : null}
              Сохранить
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
