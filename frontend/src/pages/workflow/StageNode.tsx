import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import { CloudUpload, Flag, Target, Timer, Undo2 } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { SURFACE } from '@/shared/config/tokens';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { NODE_HEIGHT, NODE_WIDTH, type DraftStage } from './model';

/**
 * Узел «Этап» (redesign.md §6.6): карточка bg-card rounded-md shadow-card
 * с полосой 4px цвета этапа, счётчиком заявок и иконками-маркерами lucide
 * (Flag начальный, Target терминальный, Timer порог, CloudUpload LMS).
 * Появление узла — scale 0.95→1 springs.gentle; выбранный — ring-2 ring-primary.
 * Удалённый в черновике этап — «призрак»: полупрозрачный, серый, зачёркнут.
 */

export type StageNodeData = {
  stage: DraftStage;
  /** Заявок сейчас на этапе (null — данные ещё не загружены). */
  count: number | null;
  /** Счётчик приблизительный (список заявок обрезан лимитом выборки). */
  approxCount: boolean;
  /** Помечен на удаление в черновике. */
  ghost: boolean;
  /** Название этапа, куда мигрируют заявки (для призрака). */
  migrateToName?: string;
  /** Есть ошибка валидации схемы — красная рамка. */
  hasIssue: boolean;
  editing: boolean;
};

export type StageFlowNode = Node<StageNodeData, 'stage'>;

function Marker({
  tooltip,
  children,
}: {
  tooltip: string;
  children: ReactNode;
}): ReactNode {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex" aria-label={tooltip}>
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

export function StageNode({ data, selected }: NodeProps<StageFlowNode>): ReactNode {
  const { stage, count, approxCount, ghost, migrateToName, hasIssue, editing } = data;
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={springs.gentle}
      className={cn(
        'flex overflow-hidden rounded-md bg-card shadow-card',
        selected && !hasIssue && 'ring-2 ring-primary',
        hasIssue && 'ring-2 ring-status-danger',
        !selected && !hasIssue && 'border border-border',
        ghost && 'border-dashed opacity-45 grayscale',
      )}
      style={{ width: NODE_WIDTH, minHeight: NODE_HEIGHT }}
    >
      <div className="w-1 shrink-0" style={{ background: stage.color }} aria-hidden="true" />
      <div className="min-w-0 flex-1 px-2.5 py-2">
        <div className="flex items-center gap-1.5">
          <span
            className={cn('min-w-0 flex-1 truncate text-[13px] font-medium', ghost && 'line-through')}
            title={stage.name}
          >
            {stage.name}
          </span>
          {stage.is_initial ? (
            <Marker tooltip="Начальный этап">
              <Flag className="size-3.5 shrink-0 text-status-success-deep" aria-hidden="true" />
            </Marker>
          ) : null}
          {stage.is_terminal ? (
            <Marker
              tooltip={
                stage.terminal_outcome === 'won'
                  ? 'Терминальный этап: успех'
                  : 'Терминальный этап: отказ'
              }
            >
              <Target
                className={cn(
                  'size-3.5 shrink-0',
                  stage.terminal_outcome === 'won'
                    ? 'text-status-success-deep'
                    : 'text-status-danger-deep',
                )}
                aria-hidden="true"
              />
            </Marker>
          ) : null}
        </div>
        <div className="mt-1 flex items-center gap-2 text-[11px] text-muted-foreground">
          <span className="tabular">
            заявок: {count === null ? '…' : `${approxCount ? '≈' : ''}${count}`}
          </span>
          {stage.stuck_threshold_days != null ? (
            <Marker tooltip={`Порог зависания: ${stage.stuck_threshold_days} дн.`}>
              <span className="inline-flex items-center gap-0.5">
                <Timer className="size-3" aria-hidden="true" />
                {stage.stuck_threshold_days}д
              </span>
            </Marker>
          ) : null}
          {stage.triggers_lms_handover ? (
            <Marker tooltip="Вход в этап передаёт заявку в LMS">
              <CloudUpload className="size-3 text-primary" aria-hidden="true" />
            </Marker>
          ) : null}
        </div>
        {ghost ? (
          <div className="mt-0.5 flex items-center gap-1 text-[11px] text-status-danger-deep">
            <Undo2 className="size-3" aria-hidden="true" />
            будет удалён{migrateToName ? ` → «${migrateToName}»` : ''}
          </div>
        ) : null}
      </div>
      <Handle
        type="target"
        position={Position.Left}
        isConnectable={editing && !ghost}
        style={{ width: 9, height: 9, background: SURFACE.mutedForeground }}
      />
      <Handle
        type="source"
        position={Position.Right}
        isConnectable={editing && !ghost && !stage.is_terminal}
        style={{ width: 9, height: 9, background: SURFACE.primary }}
      />
    </motion.div>
  );
}
