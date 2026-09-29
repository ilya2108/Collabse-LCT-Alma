import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Edge,
  type EdgeProps,
} from '@xyflow/react';
import { MessageSquareText, Undo2 } from 'lucide-react';
import type { ReactNode } from 'react';
import { STATUS_TRIPLES, SURFACE } from '@/shared/config/tokens';
import { NODE_HEIGHT, type DraftTransition } from './model';

/**
 * Ребро-переход (redesign.md §6.6): smoothstep со скруглёнными углами.
 * Обычный переход — сплошной primary со стрелкой; возврат (`kind: return`) —
 * danger-пунктир, уводится ортогональной петлёй ниже узлов, чтобы не
 * пересекать основную цепочку. Подпись — плашка bg-card со скруглением и
 * бордером (не сливается с линиями). Выбранное ребро анимируется бегущим
 * пунктиром (класс wf-edge-selected, keyframes в WorkflowConstructorPage; при
 * prefers-reduced-motion анимация отключена там же). Hex-строки — из
 * shared/config/tokens (для SVG-атрибутов xyflow токены-строки разрешены §1.1).
 */

export type TransitionEdgeData = {
  transition: DraftTransition;
};

export type TransitionFlowEdge = Edge<TransitionEdgeData, 'transition'>;

export function TransitionEdge({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
  markerEnd,
  style,
}: EdgeProps<TransitionFlowEdge>): ReactNode {
  const transition = data?.transition;
  const isReturn = transition?.kind === 'return';
  const color = isReturn ? STATUS_TRIPLES.danger.core : SURFACE.primary;

  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
    borderRadius: 12,
    // возврат уводим ниже узлов: горизонтальный сегмент петли (и подпись на
    // нём) не налезает на рёбра основной цепочки
    ...(isReturn ? { centerY: Math.max(sourceY, targetY) + 88 } : {}),
  });

  return (
    <>
      <BaseEdge
        path={path}
        markerEnd={markerEnd}
        className={selected ? 'wf-edge-selected' : undefined}
        style={{
          stroke: color,
          strokeWidth: selected ? 2.4 : 1.5,
          strokeDasharray: selected ? '6 4' : isReturn ? '6 4' : undefined,
          ...style,
        }}
      />
      {/* Между соседними узлами цепочки плашке не хватает места (просвет уже
          самой плашки) — там подпись показывается только у выбранного ребра,
          поверх узлов; длинные/наклонные рёбра и возвраты подписаны всегда. */}
      {transition &&
      (selected ||
        isReturn ||
        Math.abs(targetX - sourceX) >= 170 ||
        Math.abs(targetY - sourceY) >= NODE_HEIGHT / 2) ? (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none absolute inline-flex max-w-[170px] items-center gap-1 truncate rounded-md border bg-card px-2 py-0.5 text-[11px] shadow-card"
            title={transition.name}
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              ...(selected ? { background: SURFACE.primaryTint, zIndex: 1000 } : {}),
              borderColor: selected ? color : SURFACE.border,
              color: isReturn ? STATUS_TRIPLES.danger.deep : SURFACE.foreground,
            }}
          >
            {isReturn ? <Undo2 className="size-3 shrink-0" aria-hidden="true" /> : null}
            <span className="truncate">
              {isReturn ? `возврат · ${transition.name}` : transition.name}
            </span>
            {transition.requires_comment ? (
              <MessageSquareText className="size-2.5 shrink-0" aria-hidden="true" />
            ) : null}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}
