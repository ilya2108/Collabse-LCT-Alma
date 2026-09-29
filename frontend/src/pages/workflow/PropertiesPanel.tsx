import { zodResolver } from '@hookform/resolvers/zod';
import { Pencil, Trash2, TriangleAlert, Undo2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import type { WorkflowSchema } from '@/shared/api/types';
import { ROLE_LABELS } from '@/shared/auth/roles';
import { cn } from '@/shared/lib/cn';
import { LocalSegmented } from '@/components/wp5/PageChrome';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/shared/ui/form';
import { Input } from '@/shared/ui/input';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import { Separator } from '@/shared/ui/separator';
import { Switch } from '@/shared/ui/switch';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import {
  STAGE_COLOR_PRESETS,
  aliveTransitions,
  isLocalId,
  type DraftStage,
  type DraftTransition,
  type ValidationIssue,
  type WorkflowDraft,
} from './model';

/**
 * Правая панель конструктора (redesign.md §6.6): свойства выбранного
 * узла/ребра на новых контролах (rhf+zod §5.2); выбор цвета — 8 свотчей §1.2;
 * ничего не выбрано — StatCard-мини «этапов/переходов/заявок» и список ошибок
 * валидации (клик по ошибке фокусирует узел).
 */

export interface CanvasSelection {
  kind: 'stage' | 'transition';
  id: string;
}

const TRANSITION_ROLES = ['kam', 'head_kam', 'admin'] as const;

/** Русские имена пресетных цветов этапа (§1.2) — для aria-label свотчей. */
const COLOR_NAMES: Record<string, string> = {
  '#1E56A0': 'синий',
  '#C46A00': 'оранжевый',
  '#12917E': 'морской',
  '#8250C8': 'фиолетовый',
  '#C13B63': 'малиновый',
  '#946300': 'охра',
  '#2E9E5B': 'зелёный',
  '#5A6B80': 'серый',
};

interface PropertiesPanelProps {
  editing: boolean;
  schema: WorkflowSchema;
  draft: WorkflowDraft | null;
  selection: CanvasSelection | null;
  issues: ValidationIssue[];
  opsCount: number;
  counts: Map<string, number>;
  countsApprox: boolean;
  countsReady: boolean;
  onFocusStage: (stageId: string) => void;
  onPatchStage: (stageId: string, patch: Partial<DraftStage>) => void;
  onRequestRename: (stageId: string) => void;
  onResetRename: (stageId: string) => void;
  onRequestDelete: (stageId: string) => void;
  onRemoveNewStage: (stageId: string) => void;
  onRestoreStage: (stageId: string) => void;
  onPatchTransition: (transitionId: string, patch: Partial<DraftTransition>) => void;
  onRemoveTransition: (transitionId: string) => void;
}

function ColorSwatches({
  value,
  onChange,
}: {
  value: string;
  onChange: (color: string) => void;
}): ReactNode {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label="Цвет этапа">
      {STAGE_COLOR_PRESETS.map((color) => (
        <button
          key={color}
          type="button"
          onClick={() => onChange(color)}
          aria-label={`Цвет: ${COLOR_NAMES[color] ?? color}`}
          aria-pressed={value === color}
          className={cn(
            'size-6 rounded-md transition-[box-shadow] duration-150',
            value === color && 'ring-2 ring-ring ring-offset-2 ring-offset-card',
          )}
          style={{ background: color }}
        />
      ))}
    </div>
  );
}

function FieldBlock({ label, children }: { label: ReactNode; children: ReactNode }): ReactNode {
  return (
    <div className="space-y-1.5">
      <span className="block text-xs text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

/** Мини-плитка сводки (StatCard-мини §6.6). */
function MiniStat({ value, label }: { value: ReactNode; label: string }): ReactNode {
  return (
    <div className="rounded-md bg-muted px-3 py-2 text-center">
      <div className="tabular text-lg font-semibold leading-6">{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  );
}

// --- Форма этапа (rhf + zod, §5.2) -------------------------------------------

const newStageSchema = z.object({
  name: z.string().trim().min(1, 'Укажите название этапа').max(80, 'Не более 80 символов'),
  code: z
    .string()
    .min(1, 'Укажите код')
    .max(40, 'Не более 40 символов')
    .regex(/^[a-z0-9_]+$/, 'Латиница строчными, цифры и «_»'),
});

function NewStageFields({
  stage,
  onPatchStage,
}: {
  stage: DraftStage;
  onPatchStage: (stageId: string, patch: Partial<DraftStage>) => void;
}): ReactNode {
  const form = useForm<z.infer<typeof newStageSchema>>({
    resolver: zodResolver(newStageSchema),
    mode: 'onChange',
    defaultValues: { name: stage.name, code: stage.code },
  });
  return (
    <Form {...form}>
      <div className="space-y-3">
        <FormField
          control={form.control}
          name="name"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Название</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  maxLength={80}
                  autoComplete="off"
                  placeholder="Например: Первый контакт…"
                  onChange={(event) => {
                    field.onChange(event);
                    if (event.target.value.trim().length > 0) {
                      onPatchStage(stage.id, { name: event.target.value });
                    }
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="code"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Код (латиницей, для интеграций)</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  maxLength={40}
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="Например: first_contact…"
                  onChange={(event) => {
                    const sanitized = event.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9_]/g, '_');
                    field.onChange(sanitized);
                    if (sanitized.length > 0) onPatchStage(stage.id, { code: sanitized });
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
    </Form>
  );
}

const numbersSchema = z.object({
  stuck: z
    .string()
    .refine(
      (value) => value === '' || (/^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 60),
      'От 1 до 60 дней; пусто — наследуется',
    ),
  position: z.string().refine((value) => /^\d+$/.test(value), 'Целое число от 0'),
});

function StageNumberFields({
  stage,
  onPatchStage,
}: {
  stage: DraftStage;
  onPatchStage: (stageId: string, patch: Partial<DraftStage>) => void;
}): ReactNode {
  const form = useForm<z.infer<typeof numbersSchema>>({
    resolver: zodResolver(numbersSchema),
    mode: 'onChange',
    defaultValues: {
      stuck: stage.stuck_threshold_days != null ? String(stage.stuck_threshold_days) : '',
      position: String(stage.position),
    },
  });
  return (
    <Form {...form}>
      <div className="space-y-3">
        <FormField
          control={form.control}
          name="stuck"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Порог зависания, дней (пусто — наследуется)</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="number"
                  inputMode="numeric"
                  min={1}
                  max={60}
                  placeholder="наследуется от процесса…"
                  onChange={(event) => {
                    field.onChange(event);
                    const value = event.target.value;
                    if (value === '') {
                      onPatchStage(stage.id, { stuck_threshold_days: null });
                    } else if (/^\d+$/.test(value) && Number(value) >= 1 && Number(value) <= 60) {
                      onPatchStage(stage.id, { stuck_threshold_days: Number(value) });
                    }
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="position"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Порядок колонки на доске</FormLabel>
              <FormControl>
                <Input
                  {...field}
                  type="number"
                  inputMode="numeric"
                  min={0}
                  onChange={(event) => {
                    field.onChange(event);
                    const value = event.target.value;
                    if (/^\d+$/.test(value)) {
                      onPatchStage(stage.id, { position: Number(value) });
                    }
                  }}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
      </div>
    </Form>
  );
}

const transitionSchema = z.object({
  name: z.string().trim().min(1, 'Укажите название действия').max(80, 'Не более 80 символов'),
});

function TransitionNameField({
  transition,
  onPatchTransition,
}: {
  transition: DraftTransition;
  onPatchTransition: (transitionId: string, patch: Partial<DraftTransition>) => void;
}): ReactNode {
  const form = useForm<z.infer<typeof transitionSchema>>({
    resolver: zodResolver(transitionSchema),
    mode: 'onChange',
    defaultValues: { name: transition.name },
  });
  return (
    <Form {...form}>
      <FormField
        control={form.control}
        name="name"
        render={({ field }) => (
          <FormItem>
            <FormLabel>Название действия (кнопка в карточке и на доске)</FormLabel>
            <FormControl>
              <Input
                {...field}
                maxLength={80}
                autoComplete="off"
                placeholder="Например: Передать на согласование…"
                onChange={(event) => {
                  field.onChange(event);
                  if (event.target.value.trim().length > 0) {
                    onPatchTransition(transition.id, { name: event.target.value });
                  }
                }}
              />
            </FormControl>
            <FormMessage />
          </FormItem>
        )}
      />
    </Form>
  );
}

// --- Панель ---------------------------------------------------------------------

export function PropertiesPanel(props: PropertiesPanelProps): ReactNode {
  const { editing, schema, draft, selection, issues } = props;
  const [confirmRemove, setConfirmRemove] = useState<
    { kind: 'stage' | 'transition'; id: string } | null
  >(null);

  const stages: DraftStage[] =
    draft?.stages ?? schema.statuses.map((s) => ({ ...s, ui_position: s.ui_position ?? null }));
  const transitions: DraftTransition[] = draft?.transitions ?? schema.transitions;
  const stageById = new Map(stages.map((s) => [s.id, s]));
  const baseStageById = new Map(schema.statuses.map((s) => [s.id, s]));

  let content: ReactNode;
  if (selection?.kind === 'stage' && stageById.has(selection.id)) {
    const stage = stageById.get(selection.id) as DraftStage;
    const baseStage = baseStageById.get(stage.id);
    const isNew = isLocalId(stage.id);
    const ghost = Boolean(draft?.deleted[stage.id]);
    const count = props.counts.get(stage.id) ?? 0;
    const countLabel = props.countsReady ? `${props.countsApprox ? '≈' : ''}${count}` : '…';

    content = (
      <div className="space-y-4">
        <h2 className="text-base font-semibold text-balance">Этап «{stage.name}»</h2>
        {ghost ? (
          <>
            <Alert variant="destructive">
              <Trash2 aria-hidden="true" />
              <AlertTitle>Этап помечен на удаление</AlertTitle>
              <AlertDescription>
                Заявки будут перенесены в «
                {stageById.get(draft?.deleted[stage.id]?.migrate_to_status_id ?? '')?.name ?? '…'}
                » после согласования администратором.
              </AlertDescription>
            </Alert>
            <Button variant="outline" onClick={() => props.onRestoreStage(stage.id)}>
              <Undo2 aria-hidden="true" /> Восстановить этап
            </Button>
          </>
        ) : !editing ? (
          <dl className="grid grid-cols-[130px_1fr] gap-y-2 text-sm">
            <dt className="text-muted-foreground">Код</dt>
            <dd className="font-mono text-xs leading-5">{stage.code}</dd>
            <dt className="text-muted-foreground">Порог зависания</dt>
            <dd>{stage.stuck_threshold_days ?? 'наследуется'}</dd>
            <dt className="text-muted-foreground">Заявок сейчас</dt>
            <dd className="tabular">{countLabel}</dd>
          </dl>
        ) : (
          <>
            {isNew ? (
              <NewStageFields
                key={stage.id}
                stage={stage}
                onPatchStage={props.onPatchStage}
              />
            ) : (
              <FieldBlock label="Название">
                <div className="flex gap-2">
                  <Input value={stage.name} readOnly className="bg-muted" />
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button variant="outline" onClick={() => props.onRequestRename(stage.id)}>
                        <Pencil aria-hidden="true" /> Переименовать
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>
                      Переименование опубликованного этапа — через согласование администратора
                    </TooltipContent>
                  </Tooltip>
                </div>
                {baseStage && baseStage.name !== stage.name ? (
                  <p className="flex items-start gap-1 text-xs text-status-warning-deep">
                    <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden="true" />
                    <span>
                      Требует согласования. Было: «{baseStage.name}» —{' '}
                      <button
                        type="button"
                        className="text-primary underline-offset-2 hover:underline"
                        onClick={() => props.onResetRename(stage.id)}
                      >
                        отменить
                      </button>
                    </span>
                  </p>
                ) : null}
              </FieldBlock>
            )}

            <FieldBlock label="Цвет этапа">
              <ColorSwatches
                value={stage.color}
                onChange={(color) => props.onPatchStage(stage.id, { color })}
              />
            </FieldBlock>

            <div className="space-y-2">
              {/* Смена начального этапа и терминальности существующего этапа
                  через пакет операций не поддерживается — флаги read-only,
                  редактируются только у нового этапа (кроме is_initial). */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <label className="flex items-center gap-2 text-sm opacity-60">
                    <Checkbox checked={stage.is_initial} disabled />
                    Начальный этап (новые заявки создаются здесь)
                  </label>
                </TooltipTrigger>
                <TooltipContent>
                  Начальный этап фиксирован схемой — сменить его через конструктор нельзя
                </TooltipContent>
              </Tooltip>
              <label
                className={cn('flex items-center gap-2 text-sm', !isNew && 'opacity-60')}
                title={
                  isNew
                    ? undefined
                    : 'Терминальность опубликованного этапа изменить нельзя — добавьте новый терминальный этап'
                }
              >
                <Checkbox
                  checked={stage.is_terminal}
                  disabled={!isNew}
                  onCheckedChange={(checked) =>
                    props.onPatchStage(stage.id, {
                      is_terminal: checked === true,
                      terminal_outcome: checked === true ? stage.terminal_outcome ?? 'won' : null,
                    })
                  }
                />
                Терминальный этап (конец процесса)
              </label>
              {stage.is_terminal ? (
                <RadioGroup
                  value={stage.terminal_outcome ?? 'won'}
                  disabled={!isNew}
                  onValueChange={(value) =>
                    props.onPatchStage(stage.id, {
                      terminal_outcome: value as 'won' | 'lost',
                    })
                  }
                  className="ml-6 gap-1.5"
                >
                  <label className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="won" /> Успех (won)
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <RadioGroupItem value="lost" /> Отказ (lost)
                  </label>
                </RadioGroup>
              ) : null}
            </div>

            <StageNumberFields key={`nums-${stage.id}`} stage={stage} onPatchStage={props.onPatchStage} />

            <FieldBlock label="Интеграция с LMS">
              <label className="flex items-center gap-2 text-sm">
                <Switch
                  checked={stage.triggers_lms_handover}
                  onCheckedChange={(checked) =>
                    props.onPatchStage(stage.id, { triggers_lms_handover: checked })
                  }
                />
                Передавать заявку в LMS при входе в этап
              </label>
            </FieldBlock>

            <p className="tabular text-xs text-muted-foreground">Заявок сейчас: {countLabel}</p>
            <Separator />
            {isNew ? (
              <Button variant="destructive" size="sm" onClick={() => setConfirmRemove({ kind: 'stage', id: stage.id })}>
                <Trash2 aria-hidden="true" /> Убрать новый этап
              </Button>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="inline-flex">
                    <Button
                      variant="destructive"
                      size="sm"
                      disabled={stage.is_initial}
                      onClick={() => props.onRequestDelete(stage.id)}
                    >
                      <Trash2 aria-hidden="true" /> Удалить этап
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  {stage.is_initial
                    ? 'Начальный этап удалить нельзя'
                    : 'Опасная операция: потребуется согласование администратора'}
                </TooltipContent>
              </Tooltip>
            )}
          </>
        )}
      </div>
    );
  } else if (selection?.kind === 'transition') {
    const transition = transitions.find((t) => t.id === selection.id);
    if (!transition) {
      content = <p className="text-sm text-muted-foreground">Переход не найден</p>;
    } else {
      const from = stageById.get(transition.from_status_id);
      const to = stageById.get(transition.to_status_id);
      content = (
        <div className="space-y-4">
          <h2 className="text-base font-semibold text-balance">
            Переход «{from?.name ?? '…'}» → «{to?.name ?? '…'}»
          </h2>
          {!editing ? (
            <dl className="grid grid-cols-[90px_1fr] gap-y-2 text-sm">
              <dt className="text-muted-foreground">Действие</dt>
              <dd>{transition.name}</dd>
              <dt className="text-muted-foreground">Тип</dt>
              <dd>{transition.kind === 'return' ? 'возврат (с комментарием)' : 'обычный'}</dd>
              <dt className="text-muted-foreground">Роли</dt>
              <dd>
                {transition.allowed_roles.length > 0
                  ? transition.allowed_roles
                      .map((r) => ROLE_LABELS[r as keyof typeof ROLE_LABELS] ?? r)
                      .join(', ')
                  : 'все (по доступу к заявке)'}
              </dd>
            </dl>
          ) : (
            <>
              <TransitionNameField
                key={transition.id}
                transition={transition}
                onPatchTransition={props.onPatchTransition}
              />
              <FieldBlock label="Тип перехода">
                <LocalSegmented
                  aria-label="Тип перехода"
                  value={transition.kind}
                  onChange={(value) =>
                    props.onPatchTransition(transition.id, {
                      kind: value as 'forward' | 'return',
                    })
                  }
                  options={[
                    { value: 'forward', label: 'Обычный' },
                    { value: 'return', label: '↩ Возврат' },
                  ]}
                />
              </FieldBlock>
              <label
                className={cn(
                  'flex items-center gap-2 text-sm',
                  transition.kind === 'return' && 'opacity-60',
                )}
              >
                <Checkbox
                  checked={transition.requires_comment}
                  disabled={transition.kind === 'return'}
                  onCheckedChange={(checked) =>
                    props.onPatchTransition(transition.id, { requires_comment: checked === true })
                  }
                />
                Требуется комментарий
                {transition.kind === 'return' ? ' (для возврата — всегда)' : ''}
              </label>
              <FieldBlock label="Кому доступен переход (никого — всем по доступу к заявке)">
                <div className="space-y-1.5">
                  {TRANSITION_ROLES.map((role) => (
                    <label key={role} className="flex items-center gap-2 text-sm">
                      <Checkbox
                        checked={transition.allowed_roles.includes(role)}
                        onCheckedChange={(checked) => {
                          const next =
                            checked === true
                              ? [...transition.allowed_roles, role]
                              : transition.allowed_roles.filter((r) => r !== role);
                          props.onPatchTransition(transition.id, { allowed_roles: next });
                        }}
                      />
                      {ROLE_LABELS[role]}
                    </label>
                  ))}
                </div>
              </FieldBlock>
              <Separator />
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setConfirmRemove({ kind: 'transition', id: transition.id })}
              >
                <Trash2 aria-hidden="true" /> Удалить переход
              </Button>
            </>
          )}
        </div>
      );
    }
  } else {
    // сводка схемы
    const alive = draft ? draft.stages.filter((s) => !draft.deleted[s.id]) : stages;
    const aliveTr = draft ? aliveTransitions(draft) : transitions;
    const returns = aliveTr.filter((t) => t.kind === 'return').length;
    const totalRequests = props.countsReady
      ? alive.reduce((sum, s) => sum + (props.counts.get(s.id) ?? 0), 0)
      : null;
    content = (
      <div className="space-y-4">
        <h2 className="text-base font-semibold text-balance">Схема «{schema.name}»</h2>
        <div className="grid grid-cols-3 gap-2">
          <MiniStat value={alive.length} label="этапов" />
          <MiniStat value={aliveTr.length} label="переходов" />
          <MiniStat
            value={totalRequests === null ? '…' : `${props.countsApprox ? '≈' : ''}${totalRequests}`}
            label="заявок"
          />
        </div>
        {returns > 0 ? (
          <p className="text-xs text-muted-foreground">Из них возвратов: {returns}</p>
        ) : null}
        {editing ? (
          <p className="tabular text-sm text-muted-foreground">
            Операций в пакете: {props.opsCount}
          </p>
        ) : null}
        {editing && issues.length > 0 ? (
          <div className="rounded-md bg-status-danger-tint p-3">
            <p className="mb-1.5 text-sm font-medium text-status-danger-deep">
              Ошибки схемы: {issues.length}
            </p>
            <ul className="space-y-1">
              {issues.map((issue, index) => (
                <li key={index}>
                  {issue.stageId ? (
                    <button
                      type="button"
                      className="flex items-start gap-1.5 text-left text-sm text-status-danger-deep underline-offset-2 hover:underline"
                      onClick={() => issue.stageId && props.onFocusStage(issue.stageId)}
                    >
                      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                      {issue.message}
                    </button>
                  ) : (
                    <span className="flex items-start gap-1.5 text-sm text-status-danger-deep">
                      <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
                      {issue.message}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <Separator />
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Заявки по этапам
          </p>
          <ul className="space-y-1.5">
            {[...alive]
              .sort((a, b) => a.position - b.position)
              .map((stage) => (
                <li key={stage.id} className="flex items-center justify-between gap-2 text-sm">
                  <span className="flex min-w-0 items-center gap-2">
                    <span
                      className="size-2 shrink-0 rounded-[2px]"
                      style={{ background: stage.color }}
                      aria-hidden="true"
                    />
                    <span className="truncate">{stage.name}</span>
                  </span>
                  <span className="tabular shrink-0 text-muted-foreground">
                    {props.countsReady
                      ? `${props.countsApprox ? '≈' : ''}${props.counts.get(stage.id) ?? 0}`
                      : '…'}
                  </span>
                </li>
              ))}
          </ul>
        </div>
        {editing ? (
          <p className="text-xs text-muted-foreground">
            Кликните этап или переход, чтобы отредактировать свойства. Новый переход —
            протяжкой от правой точки этапа к левой точке другого.
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div
      data-tour="wf-properties"
      className="w-80 shrink-0 overflow-y-auto rounded-lg border bg-card p-4 shadow-card"
    >
      {content}

      <ConfirmDialog
        open={confirmRemove !== null}
        onOpenChange={(next) => (!next ? setConfirmRemove(null) : undefined)}
        tone="danger"
        title={
          confirmRemove?.kind === 'stage'
            ? 'Убрать этап из черновика?'
            : 'Удалить переход из черновика?'
        }
        confirmLabel={confirmRemove?.kind === 'stage' ? 'Убрать' : 'Удалить'}
        onConfirm={() => {
          if (!confirmRemove) return;
          if (confirmRemove.kind === 'stage') props.onRemoveNewStage(confirmRemove.id);
          else props.onRemoveTransition(confirmRemove.id);
          setConfirmRemove(null);
        }}
      />
    </div>
  );
}

/** Заголовок-подсказка режима просмотра — используется страницей конструктора. */
export function ViewOnlyHint(): ReactNode {
  return (
    <p className="text-sm text-muted-foreground">
      Режим просмотра: редактирование доступно ролям admin и head_kam
    </p>
  );
}
