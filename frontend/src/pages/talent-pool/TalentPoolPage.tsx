import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { CalendarDays, FileUp, Info, KanbanSquare, Plus, Table2 } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { RegistryTable } from '@/components/RegistryTable/RegistryTable';
import type { RegistryColumn } from '@/components/RegistryTable/types';
import { useOnboarding } from '@/features/onboarding';
import { changeStudentStatus, listStudents } from '@/shared/api/endpoints/students';
import { getTalentPoolFunnel } from '@/shared/api/endpoints/reports';
import type { Student, StudentFunnelStatus } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatRelative } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Alert, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { ProgramCombobox, UniversityCombobox } from './EntityCombobox';
import { FunnelBoard, truncatedNote } from './FunnelBoard';
import { FunnelSwitcher } from './FunnelSwitcher';
import { ErrorState, PageHeader, SegmentedControl, StatusBadge } from './local';
import { FUNNEL_META, FUNNEL_ORDER, funnelMeta, isFunnelReturn } from './model';
import { PiiValue, RevealEye } from './PiiCells';
import { SchedulePanel } from './SchedulePanel';
import { StatusChangeModal } from './StatusChangeModal';
import { StudentFormDrawer } from './StudentFormDrawer';
import { useReveal } from './useReveal';

/**
 * Talent pool (redesign.md §6.7, ux.md §12): воронка-переключатель
 * сегментами-карточками, реестр с маскированными ПДн, доска drag&drop и
 * сквозное расписание карточками. Функциональность (запросы, optimistic,
 * RBAC) не менялась — только вид.
 */

type ViewMode = 'registry' | 'board' | 'schedule';

export function TalentPoolPage(): ReactNode {
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();
  const reveal = useReveal();
  const { completeChecklistItem } = useOnboarding();
  const [createOpen, setCreateOpen] = useState(false);
  const [pendingMove, setPendingMove] = useState<{ student: Student; to: StudentFunnelStatus } | null>(
    null,
  );

  // Чек-лист онбординга observer: «Открыть talent pool» (§7.5).
  useEffect(() => {
    completeChecklistItem('open-talent-pool');
  }, [completeChecklistItem]);

  const statusParam = searchParams.get('status');
  const funnelStatus = FUNNEL_ORDER.includes(statusParam as StudentFunnelStatus)
    ? (statusParam as StudentFunnelStatus)
    : null;
  const viewParam = searchParams.get('view');
  const view: ViewMode =
    viewParam === 'board' || viewParam === 'schedule' ? viewParam : 'registry';

  const canWrite = hasRole('admin', 'head_kam', 'kam');

  const setParam = (key: string, value: string | null): void => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );
  };

  // --- Счётчики воронки (отчёт §7.2, живые данные) ---------------------------
  const funnelQuery = useQuery({
    queryKey: ['talent-pool-funnel'],
    queryFn: ({ signal }) => getTalentPoolFunnel({}, signal),
    staleTime: 30_000,
  });
  const countsByStatus = useMemo(() => {
    const map: Partial<Record<StudentFunnelStatus, number>> = {};
    for (const item of funnelQuery.data?.series ?? []) {
      if (FUNNEL_ORDER.includes(item.key as StudentFunnelStatus)) {
        map[item.key as StudentFunnelStatus] = item.value;
      }
    }
    return map;
  }, [funnelQuery.data]);

  // --- Смена статуса (доска и модалка) ---------------------------------------
  const moveMutation = useMutation({
    mutationFn: ({
      student,
      to,
      comment,
    }: {
      student: Student;
      to: StudentFunnelStatus;
      comment?: string;
    }) => changeStudentStatus(student.id, { to, comment, version: student.version }),
    meta: { silent: true },
    onSuccess: (_, { student, to }) => {
      toastSuccess(
        'Статус изменён',
        `${student.display_name}: ${funnelMeta(student.funnel_status).short} → ${FUNNEL_META[to].short}`,
      );
      setPendingMove(null);
      void queryClient.invalidateQueries({ queryKey: ['students-board'] });
      void queryClient.invalidateQueries({ queryKey: ['registry', 'talent-pool'] });
      void queryClient.invalidateQueries({ queryKey: ['talent-pool-funnel'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось изменить статус' }),
  });

  const requestMove = (student: Student, to: StudentFunnelStatus): void => {
    if (isFunnelReturn(student.funnel_status, to)) {
      setPendingMove({ student, to });
    } else {
      moveMutation.mutate({ student, to });
    }
  };

  // --- Данные доски -----------------------------------------------------------
  // limit ≤ 200: серверная пагинация (§1.5) ограничивает размер страницы,
  // limit=400 отклонялся 422 — доска оставалась пустой при живых счётчиках.
  const boardQuery = useQuery({
    queryKey: ['students-board'],
    queryFn: ({ signal }) =>
      listStudents({ limit: 200, offset: 0, filters: {}, signal }),
    enabled: view === 'board',
    staleTime: 15_000,
  });
  const boardStudents = boardQuery.data?.items ?? [];
  const boardTotal = boardQuery.data?.total;
  const boardNote = truncatedNote(boardStudents.length, boardTotal);

  // --- Колонки реестра --------------------------------------------------------
  const columns: RegistryColumn<Student>[] = [
    {
      key: 'display_name',
      title: 'ФИО',
      alwaysVisible: true,
      sorter: true,
      render: (_, student) => (
        <span className="flex items-center gap-1" data-tour="talent-pii">
          <PiiValue
            studentId={student.id}
            controller={reveal}
            masked={student.full_name || student.display_name}
            field="full_name"
            strong
          />
          <RevealEye studentId={student.id} controller={reveal} />
        </span>
      ),
    },
    {
      key: 'email',
      title: 'Email',
      render: (_, student) => (
        <PiiValue studentId={student.id} controller={reveal} masked={student.email} field="email" />
      ),
    },
    {
      key: 'university',
      title: 'Вуз',
      responsive: ['lg'],
      render: (_, student) => student.university?.name ?? '—',
    },
    {
      key: 'program',
      title: 'Программа',
      responsive: ['lg'],
      render: (_, student) => student.program?.name ?? '—',
    },
    {
      key: 'federal_project',
      title: 'Федпроект',
      responsive: ['xl'],
      render: (_, student) => student.federal_project?.name ?? '—',
    },
    {
      key: 'funnel_status',
      title: 'Статус',
      render: (_, student) => (
        <StatusBadge color={funnelMeta(student.funnel_status).color}>
          {funnelMeta(student.funnel_status).short}
        </StatusBadge>
      ),
    },
    {
      key: 'updated_at',
      title: 'Обновлён',
      sorter: true,
      responsive: ['md'],
      render: (_, student) => formatRelative(student.updated_at),
    },
  ];

  return (
    <>
      <PageHeader
        title="Пул талантов"
        subtitle="ПДн маскированы; раскрытие фиксируется в журнале аудита"
        extra={
          <>
            <SegmentedControl
              label="Режим просмотра"
              layoutId="talent-view-thumb"
              value={view}
              onChange={(value) => setParam('view', value === 'registry' ? null : value)}
              options={[
                { value: 'registry', label: 'Реестр', icon: Table2 },
                { value: 'board', label: 'Доска', icon: KanbanSquare },
                { value: 'schedule', label: 'Расписание', icon: CalendarDays },
              ]}
            />
            {canWrite ? (
              <>
                <Button variant="outline" onClick={() => navigate('/import?entity=students')}>
                  <FileUp aria-hidden="true" />
                  Импортировать список
                </Button>
                <Button onClick={() => setCreateOpen(true)}>
                  <Plus aria-hidden="true" />
                  Добавить студента
                </Button>
              </>
            ) : null}
          </>
        }
      />
      <div className="space-y-4">
        {view !== 'schedule' ? (
          <FunnelSwitcher
            active={funnelStatus}
            counts={countsByStatus}
            onSelect={(status) => setParam('status', status)}
          />
        ) : null}
        {view === 'registry' ? (
          <RegistryTable<Student>
            key={funnelStatus ?? 'all'}
            screen="talent-pool"
            columns={columns}
            rowKey="id"
            fetcher={listStudents}
            searchPlaceholder="Поиск по ФИО или email"
            defaultFilters={funnelStatus ? { funnel_status: funnelStatus } : {}}
            defaultSort={{ field: 'updated_at', order: 'desc' }}
            renderFilters={({ filters, setFilter }) => (
              <>
                <UniversityCombobox
                  value={(filters.university_id as string) || null}
                  onChange={(value) => setFilter('university_id', value ?? '')}
                />
                <ProgramCombobox
                  value={(filters.program_id as string) || null}
                  onChange={(value) => setFilter('program_id', value ?? '')}
                />
              </>
            )}
            exportEntityType="students"
            emptyIllustration="students-empty"
            emptyTitle="Студентов пока нет"
            emptyDescription="Импортируйте список из Excel или добавьте студента вручную"
            emptyAction={
              canWrite ? (
                <div className="flex flex-wrap justify-center gap-2">
                  <Button onClick={() => navigate('/import?entity=students')}>
                    Импортировать список
                  </Button>
                  <Button variant="outline" onClick={() => setCreateOpen(true)}>
                    Добавить студента
                  </Button>
                </div>
              ) : undefined
            }
            onRowClick={(student) => navigate(`/talent-pool/students/${student.id}`)}
          />
        ) : null}
        {view === 'board' ? (
          boardQuery.isError ? (
            // раньше ошибка запроса доски молча показывала пустые колонки
            <ErrorState
              error={boardQuery.error}
              title="Не удалось загрузить карточки доски"
              onRetry={() => void boardQuery.refetch()}
            />
          ) : (
            <>
              {boardNote ? (
                <Alert>
                  <Info className="size-4" aria-hidden="true" />
                  <AlertTitle>{boardNote}</AlertTitle>
                </Alert>
              ) : null}
              <FunnelBoard
                students={boardStudents}
                loading={boardQuery.isLoading}
                countsByStatus={countsByStatus}
                onMove={requestMove}
              />
            </>
          )
        ) : null}
        {view === 'schedule' ? <SchedulePanel /> : null}
      </div>
      <StudentFormDrawer open={createOpen} onClose={() => setCreateOpen(false)} />
      <StatusChangeModal
        student={pendingMove?.student ?? null}
        to={pendingMove?.to ?? null}
        submitting={moveMutation.isPending}
        onSubmit={(comment) => {
          if (pendingMove) {
            moveMutation.mutate({ ...pendingMove, comment });
          }
        }}
        onCancel={() => setPendingMove(null)}
      />
    </>
  );
}
