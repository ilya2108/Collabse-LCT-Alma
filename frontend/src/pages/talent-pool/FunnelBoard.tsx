import {
  DndContext,
  DragOverlay,
  PointerSensor,
  TouchSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core';
import type React from 'react';
import { useState, type CSSProperties, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import type { Student, StudentFunnelStatus } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { cn } from '@/shared/lib/cn';
import { toastInfo } from '@/shared/lib/toast';
import { Badge } from '@/shared/ui/badge';
import { Skeleton } from '@/shared/ui/skeleton';
import { StatusBadge } from './local';
import { FUNNEL_META, FUNNEL_ORDER, funnelMeta, isFunnelMoveAllowed } from './model';

/**
 * Воронка студентов доской (redesign.md §6.7): 4 колонки-статуса с цветами
 * воронки, drag&drop dnd-kit (логика не менялась: вперёд — сосед, назад —
 * любой с комментарием). Подсветка разрешённых колонок — ring-primary (§2.3),
 * запрещённый дроп — только toast.
 */

type DropState = 'idle' | 'allowed' | 'forbidden' | 'source';

interface StudentCardProps {
  student: Student;
  draggable: boolean;
  overlay?: boolean;
  onClick?: () => void;
}

function StudentCard({ student, draggable, overlay = false, onClick }: StudentCardProps): ReactNode {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: overlay ? `overlay-${student.id}` : student.id,
    data: { student },
    disabled: !draggable,
  });

  const style: CSSProperties = {
    transform: transform ? `translate(${transform.x}px, ${transform.y}px)` : undefined,
    // funnelMeta: неизвестный статус с бэка не роняет карточку (draft-серый фолбэк).
    borderLeftColor: funnelMeta(student.funnel_status).color,
  };

  return (
    <div
      ref={overlay ? undefined : setNodeRef}
      style={style}
      onClick={onClick}
      {...(!overlay && !draggable
        ? {
            role: 'button',
            tabIndex: 0,
            onKeyDown: (e: React.KeyboardEvent) => {
              if (e.key === 'Enter' && onClick) onClick();
            },
          }
        : {})}
      className={cn(
        'mb-2 rounded-md border-l-[3px] bg-card p-3 shadow-card transition-[box-shadow,transform,opacity] duration-150',
        draggable ? 'cursor-grab active:cursor-grabbing' : 'cursor-pointer',
        !overlay && 'hover:-translate-y-0.5 hover:shadow-overlay',
        overlay && 'scale-[1.02] shadow-drag',
        isDragging && 'opacity-35',
      )}
      {...(draggable && !overlay ? { ...listeners, ...attributes } : {})}
    >
      <div className="truncate text-sm font-medium">{student.display_name}</div>
      <div className="truncate text-xs text-muted-foreground">
        {student.university?.name ?? 'Вуз не указан'}
      </div>
      {student.program?.name ? (
        <div className="mt-1.5">
          <StatusBadge status="draft" size="sm">
            {student.program.name}
          </StatusBadge>
        </div>
      ) : null}
    </div>
  );
}

interface FunnelColumnProps {
  status: StudentFunnelStatus;
  students: Student[];
  totalCount: number | null;
  dropState: DropState;
  draggable: boolean;
  onCardClick: (student: Student) => void;
}

function FunnelColumn({
  status,
  students,
  totalCount,
  dropState,
  draggable,
  onCardClick,
}: FunnelColumnProps): ReactNode {
  const { setNodeRef, isOver } = useDroppable({ id: status });
  const meta = FUNNEL_META[status];

  return (
    <div
      ref={setNodeRef}
      className={cn(
        'min-h-80 w-[280px] shrink-0 snap-start rounded-lg bg-muted/60 p-3 transition-[opacity,box-shadow,background-color] duration-150',
        dropState === 'allowed' && 'ring-2 ring-primary/60',
        dropState === 'allowed' && isOver && 'bg-primary-tint',
        dropState === 'forbidden' && 'opacity-50',
      )}
    >
      <div className="mb-3 flex items-center gap-2">
        <span
          className="size-2 shrink-0 rounded-full ring-1 ring-border"
          style={{ backgroundColor: meta.color }}
          aria-hidden="true"
        />
        <span className="truncate text-sm font-semibold">{meta.label}</span>
        <Badge variant="secondary" className="tabular">
          {totalCount ?? students.length}
        </Badge>
      </div>
      {students.length === 0 ? (
        <p className="py-6 text-center text-xs text-muted-foreground">Пусто</p>
      ) : (
        students.map((student) => (
          <StudentCard
            key={student.id}
            student={student}
            draggable={draggable}
            onClick={() => onCardClick(student)}
          />
        ))
      )}
    </div>
  );
}

interface FunnelBoardProps {
  students: Student[];
  loading: boolean;
  /** Точные счётчики из отчёта воронки (могут быть больше загруженных карточек). */
  countsByStatus: Partial<Record<StudentFunnelStatus, number>>;
  onMove: (student: Student, to: StudentFunnelStatus) => void;
}

export function FunnelBoard({ students, loading, countsByStatus, onMove }: FunnelBoardProps): ReactNode {
  const { hasRole } = useAuth();
  const navigate = useNavigate();
  const [active, setActive] = useState<Student | null>(null);

  const canDrag = hasRole('admin', 'head_kam', 'kam');
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 250, tolerance: 8 } }),
  );

  const byStatus = new Map<StudentFunnelStatus, Student[]>(
    FUNNEL_ORDER.map((status) => [status, []]),
  );
  for (const student of students) {
    byStatus.get(student.funnel_status)?.push(student);
  }

  const dropStateFor = (status: StudentFunnelStatus): DropState => {
    if (!active) return 'idle';
    if (status === active.funnel_status) return 'source';
    return isFunnelMoveAllowed(active.funnel_status, status) ? 'allowed' : 'forbidden';
  };

  const handleDragStart = (event: DragStartEvent): void => {
    const student = event.active.data.current?.student as Student | undefined;
    setActive(student ?? null);
  };

  const handleDragEnd = (event: DragEndEvent): void => {
    const student = active;
    setActive(null);
    if (!student || !event.over) return;
    const to = event.over.id as StudentFunnelStatus;
    if (to === student.funnel_status) return;
    if (!isFunnelMoveAllowed(student.funnel_status, to)) {
      const nextIndex = Math.min(
        FUNNEL_ORDER.indexOf(student.funnel_status) + 1,
        FUNNEL_ORDER.length - 1,
      );
      toastInfo(
        'Переход недоступен',
        `Вперёд можно перевести только на соседний статус — «${FUNNEL_META[FUNNEL_ORDER[nextIndex]!].short}»`,
      );
      return;
    }
    onMove(student, to);
  };

  if (loading) {
    return (
      <div className="flex gap-4 overflow-x-auto pb-2" aria-hidden="true">
        {FUNNEL_ORDER.map((status) => (
          <div key={status} className="w-[280px] shrink-0 space-y-3 rounded-lg bg-muted/60 p-3">
            <Skeleton className="h-4 w-2/3 shimmer" />
            <Skeleton className="h-16 shimmer" />
            <Skeleton className="h-16 shimmer" />
            <Skeleton className="h-16 shimmer" />
          </div>
        ))}
      </div>
    );
  }

  return (
    <DndContext sensors={sensors} onDragStart={handleDragStart} onDragEnd={handleDragEnd}>
      <div className="flex snap-x snap-proximity gap-4 overflow-x-auto pb-2">
        {FUNNEL_ORDER.map((status) => (
          <FunnelColumn
            key={status}
            status={status}
            students={byStatus.get(status) ?? []}
            totalCount={countsByStatus[status] ?? null}
            dropState={dropStateFor(status)}
            draggable={canDrag}
            onCardClick={(student) => navigate(`/talent-pool/students/${student.id}`)}
          />
        ))}
      </div>
      <DragOverlay dropAnimation={null}>
        {active ? <StudentCard student={active} draggable overlay /> : null}
      </DragOverlay>
    </DndContext>
  );
}

/** Если карточек в статусе больше, чем загружено, — подпись «показаны первые N». */
export function truncatedNote(loaded: number, total: number | undefined): string | null {
  if (!total || loaded >= total) return null;
  return `Показаны первые ${loaded} из ${total} — уточните фильтры или откройте реестр`;
}
