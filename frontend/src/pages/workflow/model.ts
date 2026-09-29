import type {
  UiPosition,
  WorkflowOperation,
  WorkflowSchema,
  WorkflowStatus,
  WorkflowStatusBody,
  WorkflowTransition,
  WorkflowTransitionBody,
} from '@/shared/api/types';

/**
 * Модель клиентского черновика конструктора workflow (ux.md §10, engine §6.1):
 * черновик — полная копия графа с локальными правками; при отправке из него
 * вычисляется пакет операций относительно живой схемы (add/rename/update/
 * delete_status, add/update/delete_transition).
 */

/** Префикс локальных id новых этапов/переходов (на сервере их ещё нет). */
const LOCAL_ID_PREFIX = 'local-';

export function isLocalId(id: string): boolean {
  return id.startsWith(LOCAL_ID_PREFIX);
}

export function newLocalId(): string {
  return `${LOCAL_ID_PREFIX}${crypto.randomUUID()}`;
}

export interface DraftStage extends WorkflowStatus {
  ui_position: UiPosition | null;
}

export type DraftTransition = WorkflowTransition;

/** Параметры удаления существующего этапа (red-сценарий ux.md §10.5). */
export interface StageDeleteMeta {
  /** Название этапа, введённое посимвольно, — уходит в `confirm_name`. */
  confirm_name: string;
  migrate_to_status_id: string;
}

export interface WorkflowDraft {
  workflowId: string;
  /** `updated_at` схемы на момент создания черновика — для баннера «схема изменилась». */
  baseUpdatedAt: string;
  createdAt: string;
  stages: DraftStage[];
  transitions: DraftTransition[];
  /** Существующие этапы, помеченные на удаление (на канве — «призраки»). */
  deleted: Record<string, StageDeleteMeta>;
  /** Посимвольные подтверждения переименований: status_id → введённое старое имя. */
  renameConfirms: Record<string, string>;
  /**
   * Позиции узлов, принятые за базовые (серверные либо авторазложенные при
   * создании черновика): update_status с ui_position шлём только для реально
   * передвинутых узлов.
   */
  layoutBaseline: Record<string, UiPosition>;
}

// ---------------------------------------------------------------------------
// Раскладка и создание черновика
// ---------------------------------------------------------------------------

export const NODE_WIDTH = 208;
export const NODE_HEIGHT = 76;
/** Шаг сетки канвы: фон-точки и snapToGrid в конструкторе. */
export const CANVAS_GRID = 20;
const COLUMN_GAP = 280;
/** Отступ ряда терминальных этапов от основной цепочки. */
const TERMINAL_ROW_OFFSET = 280;

const snapToGrid = (value: number): number => Math.round(value / CANVAS_GRID) * CANVAS_GRID;

/** Пресет из 8 цветов этапа (ux.md §10.3), совпадает с палитрой seed-схем. */
export const STAGE_COLOR_PRESETS = [
  '#8c8c8c',
  '#13c2c2',
  '#1677ff',
  '#722ed1',
  '#fa8c16',
  '#52c41a',
  '#237804',
  '#f5222d',
] as const;

/**
 * Автораскладка по рангам (замена BFS-уровней): основная цепочка нетерминальных
 * этапов — в линию слева направо по sort_order, терминальные («Отказ»,
 * «Завершена») — отдельным нижним рядом с воздухом, по центру входящих рёбер.
 * Координаты кратны сетке канвы (CANVAS_GRID).
 */
export function autoLayout(
  stages: readonly WorkflowStatus[],
  transitions: readonly WorkflowTransition[],
): Record<string, UiPosition> {
  const result: Record<string, UiPosition> = {};
  const chainY = 40;
  const chain = stages
    .filter((s) => !s.is_terminal)
    .sort((a, b) => a.position - b.position);
  chain.forEach((stage, index) => {
    result[stage.id] = { x: 40 + index * COLUMN_GAP, y: chainY };
  });

  const terminals = stages
    .filter((s) => s.is_terminal)
    .sort((a, b) => a.position - b.position);
  const rowY = chainY + TERMINAL_ROW_OFFSET;
  const desired = terminals.map((stage, index) => {
    const centers = transitions
      .filter((t) => t.to_status_id === stage.id)
      .map((t) => result[t.from_status_id])
      .filter((p): p is UiPosition => Boolean(p))
      .map((p) => p.x + NODE_WIDTH / 2);
    // без входящих рёбер — правее конца цепочки
    const x =
      centers.length > 0
        ? snapToGrid(centers.reduce((a, b) => a + b, 0) / centers.length - NODE_WIDTH / 2)
        : 40 + (chain.length + index) * COLUMN_GAP;
    return { stage, x };
  });
  // терминальные не налезают друг на друга: минимум COLUMN_GAP между соседями
  desired.sort((a, b) => a.x - b.x);
  let cursor = Number.NEGATIVE_INFINITY;
  for (const { stage, x } of desired) {
    const finalX = Math.max(x, cursor);
    result[stage.id] = { x: finalX, y: rowY };
    cursor = finalX + COLUMN_GAP;
  }
  return result;
}

/** Минимальный «воздух» между карточками, меньше — считаем, что налезают. */
const MIN_NODE_AIR = 24;

/**
 * Координаты вырожденные: у части узлов нет ui_position либо карточки этапов
 * налезают друг на друга (пересечение или просвет меньше MIN_NODE_AIR) —
 * доверять такой раскладке нельзя, включаем автораскладку по рангам целиком
 * (фолбэк для «странных» схем со стенда, где сид ещё со старой геометрией).
 */
export function isDegenerateLayout(
  positions: ReadonlyArray<UiPosition | null | undefined>,
): boolean {
  if (positions.some((p) => !p)) return positions.length > 0;
  const points = positions as readonly UiPosition[];
  for (let i = 0; i < points.length; i += 1) {
    for (let j = i + 1; j < points.length; j += 1) {
      if (
        Math.abs(points[i]!.x - points[j]!.x) < NODE_WIDTH + MIN_NODE_AIR &&
        Math.abs(points[i]!.y - points[j]!.y) < NODE_HEIGHT + MIN_NODE_AIR
      ) {
        return true;
      }
    }
  }
  return false;
}

/** Создать черновик из живой схемы; вырожденные координаты — авторазложение. */
export function createDraftFromSchema(schema: WorkflowSchema): WorkflowDraft {
  const layout = layoutForSchema(schema);
  const layoutBaseline: Record<string, UiPosition> = {};
  const stages: DraftStage[] = schema.statuses.map((status) => {
    const position = layout[status.id] ?? { x: 40, y: 40 };
    layoutBaseline[status.id] = position;
    return { ...status, ui_position: position };
  });
  return {
    workflowId: schema.id,
    baseUpdatedAt: schema.updated_at,
    createdAt: new Date().toISOString(),
    stages,
    transitions: schema.transitions.map((t) => ({ ...t })),
    deleted: {},
    renameConfirms: {},
    layoutBaseline,
  };
}

/** Позиции для канвы: серверные ui_position, а вырожденные — автораскладка. */
export function layoutForSchema(schema: WorkflowSchema): Record<string, UiPosition> {
  if (isDegenerateLayout(schema.statuses.map((s) => s.ui_position))) {
    return autoLayout(schema.statuses, schema.transitions);
  }
  const result: Record<string, UiPosition> = {};
  for (const status of schema.statuses) {
    result[status.id] = status.ui_position as UiPosition;
  }
  return result;
}

// ---------------------------------------------------------------------------
// Живая часть черновика (без «призраков»)
// ---------------------------------------------------------------------------

export function aliveStages(draft: WorkflowDraft): DraftStage[] {
  return draft.stages.filter((s) => !draft.deleted[s.id]);
}

export function aliveTransitions(draft: WorkflowDraft): DraftTransition[] {
  return draft.transitions.filter(
    (t) => !draft.deleted[t.from_status_id] && !draft.deleted[t.to_status_id],
  );
}

// ---------------------------------------------------------------------------
// Код (slug) нового этапа
// ---------------------------------------------------------------------------

const TRANSLIT: Record<string, string> = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i',
  й: 'y', к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't',
  у: 'u', ф: 'f', х: 'h', ц: 'ts', ч: 'ch', ш: 'sh', щ: 'sch', ъ: '', ы: 'y',
  ь: '', э: 'e', ю: 'yu', я: 'ya',
};

/** Транслит русского названия в код этапа: «Пилотный проект» → pilotnyy_proekt. */
export function slugifyStageName(name: string, takenCodes: readonly string[]): string {
  const base =
    name
      .toLowerCase()
      .split('')
      .map((ch) => TRANSLIT[ch] ?? ch)
      .join('')
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_+|_+$/g, '')
      .slice(0, 40) || 'stage';
  if (!takenCodes.includes(base)) return base;
  let n = 2;
  while (takenCodes.includes(`${base}_${n}`)) n += 1;
  return `${base}_${n}`;
}

// ---------------------------------------------------------------------------
// Валидация целевого графа (клиентское зеркало V-1..V-8 engine §6.7)
// ---------------------------------------------------------------------------

export interface ValidationIssue {
  code: string;
  message: string;
  stageId?: string;
  transitionId?: string;
}

export function validateDraft(draft: WorkflowDraft): ValidationIssue[] {
  const issues: ValidationIssue[] = [];
  const stages = aliveStages(draft);
  const transitions = aliveTransitions(draft);
  const stageById = new Map(stages.map((s) => [s.id, s]));

  const initials = stages.filter((s) => s.is_initial);
  if (initials.length !== 1) {
    issues.push({
      code: 'E_INITIAL_COUNT',
      message: `Начальный этап должен быть ровно один (сейчас: ${initials.length})`,
      stageId: initials[1]?.id,
    });
  }
  const terminals = stages.filter((s) => s.is_terminal);
  if (terminals.length === 0) {
    issues.push({ code: 'E_NO_TERMINAL', message: 'Нужен хотя бы один терминальный этап' });
  }
  for (const stage of terminals) {
    if (!stage.terminal_outcome) {
      issues.push({
        code: 'E_NO_OUTCOME',
        message: `У терминального этапа «${stage.name}» не выбран исход (успех/отказ)`,
        stageId: stage.id,
      });
    }
  }

  const seenNames = new Map<string, DraftStage>();
  const seenCodes = new Map<string, DraftStage>();
  for (const stage of stages) {
    if (!stage.name.trim()) {
      issues.push({ code: 'E_STAGE_DUP', message: 'У этапа пустое название', stageId: stage.id });
    }
    const nameKey = stage.name.trim().toLowerCase();
    if (nameKey && seenNames.has(nameKey)) {
      issues.push({
        code: 'E_STAGE_DUP',
        message: `Название «${stage.name}» повторяется у двух этапов`,
        stageId: stage.id,
      });
    }
    seenNames.set(nameKey, stage);
    if (seenCodes.has(stage.code)) {
      issues.push({
        code: 'E_STAGE_DUP',
        message: `Код «${stage.code}» повторяется у двух этапов`,
        stageId: stage.id,
      });
    }
    seenCodes.set(stage.code, stage);
  }

  const seenEdges = new Set<string>();
  for (const t of transitions) {
    if (t.from_status_id === t.to_status_id) {
      issues.push({
        code: 'E_BAD_TRANSITION',
        message: 'Переход этапа в самого себя запрещён',
        transitionId: t.id,
      });
    }
    const key = `${t.from_status_id}→${t.to_status_id}`;
    if (seenEdges.has(key)) {
      issues.push({
        code: 'E_BAD_TRANSITION',
        message: `Дублирующий переход «${stageById.get(t.from_status_id)?.name}» → «${stageById.get(t.to_status_id)?.name}»`,
        transitionId: t.id,
      });
    }
    seenEdges.add(key);
    const from = stageById.get(t.from_status_id);
    if (from?.is_terminal) {
      issues.push({
        code: 'E_TERMINAL_OUTGOING',
        message: `У терминального этапа «${from.name}» не может быть исходящих переходов`,
        stageId: from.id,
        transitionId: t.id,
      });
    }
    if (t.kind === 'return' && !t.requires_comment) {
      issues.push({
        code: 'E_RETURN_COMMENT',
        message: `Возврат «${t.name}» обязан требовать комментарий`,
        transitionId: t.id,
      });
    }
  }

  // Достижимость: из initial — все этапы; из каждого нетерминального — терминал.
  const out = new Map<string, string[]>();
  const inv = new Map<string, string[]>();
  for (const t of transitions) {
    out.set(t.from_status_id, [...(out.get(t.from_status_id) ?? []), t.to_status_id]);
    inv.set(t.to_status_id, [...(inv.get(t.to_status_id) ?? []), t.from_status_id]);
  }
  const bfs = (starts: string[], edges: Map<string, string[]>): Set<string> => {
    const visited = new Set(starts);
    const queue = [...starts];
    while (queue.length > 0) {
      const current = queue.shift() as string;
      for (const next of edges.get(current) ?? []) {
        if (!visited.has(next)) {
          visited.add(next);
          queue.push(next);
        }
      }
    }
    return visited;
  };
  if (initials.length === 1) {
    const reachable = bfs([initials[0].id], out);
    for (const stage of stages) {
      if (!reachable.has(stage.id)) {
        issues.push({
          code: 'E_UNREACHABLE_STAGE',
          message: `Этап «${stage.name}» недостижим из начального`,
          stageId: stage.id,
        });
      }
    }
  }
  if (terminals.length > 0) {
    const canFinish = bfs(terminals.map((s) => s.id), inv);
    for (const stage of stages) {
      if (!stage.is_terminal && !canFinish.has(stage.id)) {
        issues.push({
          code: 'E_NO_PATH_TO_TERMINAL',
          message: `Из этапа «${stage.name}» недостижим ни один терминальный этап`,
          stageId: stage.id,
        });
      }
    }
  }

  // V-8: цели миграции удаляемых этапов.
  for (const [stageId, meta] of Object.entries(draft.deleted)) {
    const target = stageById.get(meta.migrate_to_status_id);
    const deletedStage = draft.stages.find((s) => s.id === stageId);
    if (!target || target.is_terminal) {
      issues.push({
        code: 'E_MAPPING_INVALID',
        message: `Для удаляемого этапа «${deletedStage?.name ?? '?'}» выбран недопустимый этап переноса заявок`,
        stageId,
      });
    }
  }

  return issues;
}

// ---------------------------------------------------------------------------
// Дифф: черновик → пакет операций (api-contract.md §5.3)
// ---------------------------------------------------------------------------

function samePosition(a: UiPosition | null | undefined, b: UiPosition | null | undefined): boolean {
  if (!a || !b) return a === b;
  return Math.round(a.x) === Math.round(b.x) && Math.round(a.y) === Math.round(b.y);
}

function roundPosition(p: UiPosition | null): UiPosition | null {
  return p ? { x: Math.round(p.x), y: Math.round(p.y) } : null;
}

function stagePatch(
  base: WorkflowStatus,
  draft: DraftStage,
  baseline: UiPosition | undefined,
): Partial<Omit<WorkflowStatusBody, 'code'>> {
  const patch: Partial<Omit<WorkflowStatusBody, 'code'>> = {};
  if (draft.color !== base.color) patch.color = draft.color;
  if ((draft.stuck_threshold_days ?? null) !== (base.stuck_threshold_days ?? null)) {
    patch.stuck_threshold_days = draft.stuck_threshold_days ?? null;
  }
  if (draft.triggers_lms_handover !== base.triggers_lms_handover) {
    patch.triggers_lms_handover = draft.triggers_lms_handover;
  }
  if (draft.position !== base.position) patch.position = draft.position;
  // is_initial/is_terminal/terminal_outcome в update_status не шлём: StatusPatch
  // на сервере (extra=forbid) их не принимает — терминальность существующего
  // этапа через конструктор не меняется (UI держит эти поля read-only).
  // позицию узла шлём, только если узел реально двигали (иначе шум от авторазложения)
  const positionBase = base.ui_position ?? baseline ?? null;
  if (!samePosition(draft.ui_position, positionBase)) {
    patch.ui_position = roundPosition(draft.ui_position);
  }
  return patch;
}

function transitionPatch(
  base: WorkflowTransition,
  draft: DraftTransition,
): Partial<Omit<WorkflowTransitionBody, 'from_code' | 'to_code'>> {
  const patch: Partial<Omit<WorkflowTransitionBody, 'from_code' | 'to_code'>> = {};
  if (draft.name !== base.name) patch.name = draft.name;
  if (draft.kind !== base.kind) patch.kind = draft.kind;
  if (draft.requires_comment !== base.requires_comment) {
    patch.requires_comment = draft.requires_comment;
  }
  if (JSON.stringify([...draft.allowed_roles].sort()) !== JSON.stringify([...base.allowed_roles].sort())) {
    patch.allowed_roles = draft.allowed_roles;
  }
  return patch;
}

/** Пакет операций относительно живой схемы `base`. Порядок: add → update → delete. */
export function buildOperations(base: WorkflowSchema, draft: WorkflowDraft): WorkflowOperation[] {
  const ops: WorkflowOperation[] = [];
  const baseStageById = new Map(base.statuses.map((s) => [s.id, s]));
  const baseTransitionById = new Map(base.transitions.map((t) => [t.id, t]));
  const draftStageById = new Map(draft.stages.map((s) => [s.id, s]));
  const draftTransitionIds = new Set(draft.transitions.map((t) => t.id));

  // 1. Новые этапы
  for (const stage of draft.stages.filter((s) => isLocalId(s.id) && !draft.deleted[s.id])) {
    ops.push({
      op: 'add_status',
      status: {
        code: stage.code,
        name: stage.name.trim(),
        color: stage.color,
        // is_initial сервер в add_status не принимает (новый этап всегда не начальный)
        is_terminal: stage.is_terminal || undefined,
        terminal_outcome: stage.is_terminal ? stage.terminal_outcome : undefined,
        stuck_threshold_days: stage.stuck_threshold_days ?? null,
        triggers_lms_handover: stage.triggers_lms_handover || undefined,
        position: stage.position,
        ui_position: roundPosition(stage.ui_position),
      },
    });
  }

  // 2. Переименования и правки существующих этапов (кроме удаляемых)
  for (const stage of draft.stages) {
    if (isLocalId(stage.id) || draft.deleted[stage.id]) continue;
    const baseStage = baseStageById.get(stage.id);
    if (!baseStage) continue;
    if (stage.name.trim() !== baseStage.name) {
      ops.push({
        op: 'rename_status',
        status_id: stage.id,
        new_name: stage.name.trim(),
        confirm_name: draft.renameConfirms[stage.id] ?? baseStage.name,
      });
    }
    const patch = stagePatch(baseStage, stage, draft.layoutBaseline[stage.id]);
    if (Object.keys(patch).length > 0) {
      ops.push({ op: 'update_status', status_id: stage.id, patch });
    }
  }

  // 3. Новые переходы (между живыми этапами; адресация кодами)
  for (const t of draft.transitions.filter((t) => isLocalId(t.id))) {
    if (draft.deleted[t.from_status_id] || draft.deleted[t.to_status_id]) continue;
    const from = draftStageById.get(t.from_status_id);
    const to = draftStageById.get(t.to_status_id);
    if (!from || !to) continue;
    ops.push({
      op: 'add_transition',
      transition: {
        from_code: from.code,
        to_code: to.code,
        kind: t.kind,
        name: t.name.trim(),
        requires_comment: t.requires_comment,
        allowed_roles: t.allowed_roles,
      },
    });
  }

  // 4. Правки существующих переходов
  for (const t of draft.transitions) {
    if (isLocalId(t.id)) continue;
    const baseTransition = baseTransitionById.get(t.id);
    if (!baseTransition) continue;
    const patch = transitionPatch(baseTransition, t);
    if (Object.keys(patch).length > 0) {
      ops.push({ op: 'update_transition', transition_id: t.id, patch });
    }
  }

  // 5. Удалённые переходы (кроме уходящих вместе с удаляемым этапом — их удалит сервер)
  for (const t of base.transitions) {
    if (draftTransitionIds.has(t.id)) continue;
    if (draft.deleted[t.from_status_id] || draft.deleted[t.to_status_id]) continue;
    ops.push({ op: 'delete_transition', transition_id: t.id });
  }

  // 6. Удалённые этапы — с целью миграции и посимвольным подтверждением
  for (const [stageId, meta] of Object.entries(draft.deleted)) {
    if (isLocalId(stageId) || !baseStageById.has(stageId)) continue;
    ops.push({
      op: 'delete_status',
      status_id: stageId,
      migrate_to_status_id: meta.migrate_to_status_id,
      confirm_name: meta.confirm_name,
    });
  }

  return ops;
}

/** Опасные операции всегда уходят в pending, даже от admin (§5.3). */
export function hasDangerousOps(ops: readonly WorkflowOperation[]): boolean {
  return ops.some((op) => op.op === 'rename_status' || op.op === 'delete_status');
}

// ---------------------------------------------------------------------------
// Соседи для миграции при удалении (engine §6.3: «лучше предыдущий»)
// ---------------------------------------------------------------------------

export interface MigrateOption {
  stage: WorkflowStatus;
  direction: 'prev' | 'next';
}

/**
 * Кандидаты переноса заявок удаляемого этапа: соседи по рёбрам СТАРОГО графа,
 * нетерминальные, не удаляемые этим же пакетом. Дефолт — предыдущий сосед
 * (максимальный position меньше удаляемого).
 */
export function migrateOptionsFor(
  base: WorkflowSchema,
  draft: WorkflowDraft,
  stageId: string,
): { options: MigrateOption[]; defaultId: string | null } {
  const stage = base.statuses.find((s) => s.id === stageId);
  if (!stage) return { options: [], defaultId: null };
  const neighborIds = new Set<string>();
  for (const t of base.transitions) {
    if (t.from_status_id === stageId) neighborIds.add(t.to_status_id);
    if (t.to_status_id === stageId) neighborIds.add(t.from_status_id);
  }
  const options: MigrateOption[] = base.statuses
    .filter(
      (s) =>
        neighborIds.has(s.id) &&
        !s.is_terminal &&
        s.id !== stageId &&
        !draft.deleted[s.id],
    )
    .map((s): MigrateOption => ({
      stage: s,
      direction: s.position < stage.position ? 'prev' : 'next',
    }))
    .sort((a, b) => a.stage.position - b.stage.position);
  const prev = options.filter((o) => o.direction === 'prev').at(-1);
  const defaultId = prev?.stage.id ?? options[0]?.stage.id ?? null;
  return { options, defaultId };
}
