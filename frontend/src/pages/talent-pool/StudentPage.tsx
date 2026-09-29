import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRightLeft,
  Building2,
  ChevronDown,
  EyeOff,
  GraduationCap,
  Undo2,
} from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { FileListPanel } from '@/components/files/FileListPanel';
import {
  changeStudentStatus,
  deleteStudentFile,
  getStudent,
  listStudentFiles,
  listStudentHistory,
  uploadStudentFile,
} from '@/shared/api/endpoints/students';
import type { Student, StudentActivityItem, StudentFunnelStatus } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { dayjs } from '@/shared/lib/dayjs';
import { formatDateTime, formatText } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/ui/tabs';
import {
  ErrorState,
  LoadingBlock,
  PageHeader,
  StatusBadge,
  Timeline,
  type TimelineItem,
} from './local';
import {
  ACTIVITY_FORMAT_LABELS,
  ACTIVITY_TYPE_LABELS,
  FUNNEL_META,
  FUNNEL_ORDER,
  STUDENT_ACTIVITY_STATUS_LABELS,
  funnelIndex,
  funnelMeta,
  isFunnelReturn,
} from './model';
import { PiiValue, RevealEye } from './PiiCells';
import { StatusChangeModal } from './StatusChangeModal';
import { useReveal } from './useReveal';

/**
 * Карточка студента (redesign.md §6.7, ux.md §12.3): шапка-паспорт с
 * бейджами, маскированные ПДн с раскрытием на 60 с, табы «Профиль /
 * Расписание / Файлы / История». У observer — плашка «ПДн скрыты согласно
 * роли», таб «Файлы» скрыт (403 на backend).
 */

/** Строка DL-сетки (§5.1: замена Descriptions). */
function DlRow({ label, children }: { label: string; children: ReactNode }): ReactNode {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  );
}

export function StudentPage(): ReactNode {
  const { id } = useParams<'id'>();
  const studentId = id ?? '';
  const navigate = useNavigate();
  const { hasRole } = useAuth();
  const queryClient = useQueryClient();
  const reveal = useReveal();
  const [moveTarget, setMoveTarget] = useState<StudentFunnelStatus | null>(null);

  const canWrite = hasRole('admin', 'head_kam', 'kam');
  const isObserver = !canWrite;

  const studentQuery = useQuery({
    queryKey: ['student', studentId],
    queryFn: ({ signal }) => getStudent(studentId, signal),
    enabled: Boolean(studentId),
  });

  const historyQuery = useQuery({
    queryKey: ['student-history', studentId],
    queryFn: ({ signal }) => listStudentHistory(studentId, signal),
    enabled: Boolean(studentId),
  });

  const filesQuery = useQuery({
    queryKey: ['student-files', studentId],
    queryFn: ({ signal }) => listStudentFiles(studentId, signal),
    enabled: Boolean(studentId) && canWrite,
    retry: 0,
  });

  const moveMutation = useMutation({
    mutationFn: ({ student, to, comment }: { student: Student; to: StudentFunnelStatus; comment?: string }) =>
      changeStudentStatus(student.id, { to, comment, version: student.version }),
    meta: { silent: true },
    onSuccess: (_, { to }) => {
      toastSuccess('Статус изменён', FUNNEL_META[to].short);
      setMoveTarget(null);
      void queryClient.invalidateQueries({ queryKey: ['student', studentId] });
      void queryClient.invalidateQueries({ queryKey: ['student-history', studentId] });
      void queryClient.invalidateQueries({ queryKey: ['talent-pool-funnel'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось изменить статус' }),
  });

  if (studentQuery.isLoading) {
    return <LoadingBlock rows={10} />;
  }
  if (studentQuery.isError || !studentQuery.data) {
    return (
      <ErrorState
        error={studentQuery.error}
        title="Студент не найден или удалён"
        onRetry={() => navigate('/talent-pool')}
      />
    );
  }
  const student = studentQuery.data;
  // funnelMeta вместо FUNNEL_META[...]: неизвестный статус с бэка не роняет карточку.
  const meta = funnelMeta(student.funnel_status);

  const forward = FUNNEL_ORDER.filter(
    (status) => funnelIndex(status) === funnelIndex(student.funnel_status) + 1,
  );
  const backward = FUNNEL_ORDER.filter(
    (status) => funnelIndex(status) < funnelIndex(student.funnel_status),
  );

  const activities: StudentActivityItem[] = student.activities ?? [];

  const profileTab = (
    <div className="rounded-lg border bg-card p-5 shadow-card">
      <dl className="grid grid-cols-[140px_1fr] gap-y-2 text-sm">
        <DlRow label="ФИО">
          <span className="flex items-center gap-1">
            <PiiValue
              studentId={student.id}
              controller={reveal}
              masked={student.full_name || student.display_name}
              field="full_name"
              strong
            />
            <RevealEye studentId={student.id} controller={reveal} />
          </span>
        </DlRow>
        <DlRow label="Email">
          <PiiValue studentId={student.id} controller={reveal} masked={student.email} field="email" />
        </DlRow>
        {student.phone ? (
          <DlRow label="Телефон">
            <PiiValue studentId={student.id} controller={reveal} masked={student.phone} field="phone" />
          </DlRow>
        ) : null}
        <DlRow label="Вуз">
          {student.university ? (
            <Link
              className="text-primary hover:underline"
              to={`/registry/universities/${student.university.id}`}
            >
              {student.university.name}
            </Link>
          ) : (
            '—'
          )}
        </DlRow>
        <DlRow label="Программа">
          {student.program ? (
            <Link className="text-primary hover:underline" to={`/registry/programs/${student.program.id}`}>
              {student.program.name}
            </Link>
          ) : (
            '—'
          )}
        </DlRow>
        <DlRow label="Федпроект">
          {student.federal_project?.name ? (
            <StatusBadge status="talent" size="sm">
              {student.federal_project.name}
            </StatusBadge>
          ) : (
            '—'
          )}
        </DlRow>
        <DlRow label="Заявка B2C">
          {student.b2c_request_id ? (
            <Link className="text-primary hover:underline" to={`/requests/${student.b2c_request_id}`}>
              Открыть связанную заявку
            </Link>
          ) : (
            '—'
          )}
        </DlRow>
        <DlRow label="Источник">
          {student.source === 'import' ? 'Импорт' : student.source === 'lms' ? 'LMS' : 'Вручную'}
        </DlRow>
        <DlRow label="Примечание">{formatText(student.notes)}</DlRow>
      </dl>
    </div>
  );

  const scheduleTab = (
    <div className="rounded-lg border bg-card shadow-card">
      {activities.length === 0 ? (
        <p className="p-5 text-sm text-muted-foreground">Активности студенту пока не назначены.</p>
      ) : (
        <ul>
          {[...activities]
            .sort((a, b) => (a.starts_at ?? '').localeCompare(b.starts_at ?? ''))
            .map((item) => (
              <li
                key={item.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-4 py-2.5 text-sm first:border-t-0"
              >
                <span className="w-36 shrink-0 text-muted-foreground tabular">
                  {item.starts_at ? dayjs(item.starts_at).format('D MMM YYYY, HH:mm') : '—'}
                </span>
                <span className="min-w-0 flex-1 truncate font-medium">{item.name}</span>
                {item.activity_type ? (
                  <StatusBadge status="talent" size="sm">
                    {ACTIVITY_TYPE_LABELS[item.activity_type] ?? item.activity_type}
                  </StatusBadge>
                ) : null}
                {item.format ? (
                  <span className="text-xs text-muted-foreground">
                    {ACTIVITY_FORMAT_LABELS[item.format] ?? item.format}
                  </span>
                ) : null}
                {item.status ? (
                  <StatusBadge status="draft" size="sm">
                    {STUDENT_ACTIVITY_STATUS_LABELS[item.status] ?? item.status}
                  </StatusBadge>
                ) : null}
              </li>
            ))}
        </ul>
      )}
    </div>
  );

  const filesTab = (
    <div className="rounded-lg border bg-card p-5 shadow-card">
      {filesQuery.isError ? (
        <ErrorState
          error={filesQuery.error}
          title="Не удалось загрузить файлы"
          onRetry={() => void filesQuery.refetch()}
          compact
        />
      ) : (
        <FileListPanel
          files={filesQuery.data?.items ?? []}
          loading={filesQuery.isLoading}
          onUpload={
            canWrite
              ? async (file) => {
                  await uploadStudentFile(student.id, file);
                  await queryClient.invalidateQueries({ queryKey: ['student-files', studentId] });
                }
              : null
          }
          onDelete={
            hasRole('admin', 'head_kam')
              ? async (fileId) => {
                  await deleteStudentFile(student.id, fileId);
                  await queryClient.invalidateQueries({ queryKey: ['student-files', studentId] });
                }
              : null
          }
          emptyText="Резюме и сертификатов пока нет"
        />
      )}
    </div>
  );

  const historyItems = historyQuery.data?.items ?? [];
  const timelineItems: TimelineItem[] = historyItems.map((item) => {
    const isReturn = item.from_status ? isFunnelReturn(item.from_status, item.to_status) : false;
    return {
      id: item.id,
      icon: isReturn ? Undo2 : ArrowRightLeft,
      tone: isReturn ? 'danger' : 'talent',
      title: (
        <span className="flex flex-wrap items-center gap-1.5">
          {isReturn ? (
            <StatusBadge status="danger" size="sm" icon={Undo2}>
              Возврат
            </StatusBadge>
          ) : null}
          {item.from_status ? (
            <>
              <StatusBadge color={funnelMeta(item.from_status).color} size="sm">
                {funnelMeta(item.from_status).short}
              </StatusBadge>
              <span aria-hidden="true">→</span>
            </>
          ) : null}
          <StatusBadge color={funnelMeta(item.to_status).color} size="sm">
            {funnelMeta(item.to_status).short}
          </StatusBadge>
        </span>
      ),
      time: `${item.actor?.full_name ?? 'Система'} · ${formatDateTime(item.created_at)}`,
      content: item.reason ?? undefined,
    };
  });

  const historyTab = (
    <div className="rounded-lg border bg-card p-5 shadow-card">
      {historyQuery.isLoading ? (
        <LoadingBlock rows={4} />
      ) : timelineItems.length === 0 ? (
        <p className="text-sm text-muted-foreground">История пока пуста.</p>
      ) : (
        <Timeline items={timelineItems} />
      )}
    </div>
  );

  return (
    <>
      <PageHeader
        title={student.display_name}
        backTo="/talent-pool"
        meta={
          <>
            <StatusBadge color={meta.color}>{meta.short}</StatusBadge>
            {student.university?.name ? (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Building2 className="size-3.5" aria-hidden="true" />
                {student.university.name}
              </span>
            ) : null}
            {student.program?.name ? (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <GraduationCap className="size-3.5" aria-hidden="true" />
                {student.program.name}
              </span>
            ) : null}
          </>
        }
        extra={
          canWrite && (forward.length > 0 || backward.length > 0) ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button>
                  Перевести
                  <ChevronDown aria-hidden="true" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {forward.map((status) => (
                  <DropdownMenuItem key={status} onSelect={() => setMoveTarget(status)}>
                    <ArrowRightLeft aria-hidden="true" />
                    Перевести: {FUNNEL_META[status].short}
                  </DropdownMenuItem>
                ))}
                {forward.length > 0 && backward.length > 0 ? <DropdownMenuSeparator /> : null}
                {backward.length > 0 ? (
                  <DropdownMenuLabel className="text-xs text-muted-foreground">
                    Возврат назад
                  </DropdownMenuLabel>
                ) : null}
                {backward.map((status) => (
                  <DropdownMenuItem
                    key={status}
                    onSelect={() => setMoveTarget(status)}
                    className="text-status-danger-deep focus:text-status-danger-deep"
                  >
                    <Undo2 aria-hidden="true" />
                    Вернуть: {FUNNEL_META[status].short}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          ) : undefined
        }
      />
      {isObserver ? (
        <Alert className="mb-4">
          <EyeOff className="size-4" aria-hidden="true" />
          <AlertTitle>ПДн скрыты согласно роли</AlertTitle>
          <AlertDescription>
            Роль «Наблюдатель» видит только маскированные персональные данные; раскрытие недоступно.
          </AlertDescription>
        </Alert>
      ) : null}
      <Tabs defaultValue="profile">
        <TabsList>
          <TabsTrigger value="profile">Профиль</TabsTrigger>
          <TabsTrigger value="schedule">Расписание</TabsTrigger>
          {canWrite ? <TabsTrigger value="files">Файлы</TabsTrigger> : null}
          <TabsTrigger value="history">История</TabsTrigger>
        </TabsList>
        <TabsContent value="profile">{profileTab}</TabsContent>
        <TabsContent value="schedule">{scheduleTab}</TabsContent>
        {canWrite ? <TabsContent value="files">{filesTab}</TabsContent> : null}
        <TabsContent value="history">{historyTab}</TabsContent>
      </Tabs>
      <StatusChangeModal
        student={moveTarget ? student : null}
        to={moveTarget}
        submitting={moveMutation.isPending}
        onSubmit={(comment) => {
          if (moveTarget) moveMutation.mutate({ student, to: moveTarget, comment });
        }}
        onCancel={() => setMoveTarget(null)}
      />
    </>
  );
}
