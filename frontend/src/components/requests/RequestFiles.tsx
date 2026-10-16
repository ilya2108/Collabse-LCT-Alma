import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Archive,
  Download,
  FileText,
  Image as ImageIcon,
  Sheet as SheetIcon,
  Trash2,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useRef, useState, type ReactNode } from 'react';
import { downloadFile, formatFileSize } from '@/shared/api/endpoints/files';
import {
  REQUEST_FILE_ACCEPT,
  REQUEST_FILE_FORMATS_LABEL,
  REQUEST_FILE_MAX_MB,
  deleteRequestFile,
  listRequestFiles,
  uploadRequestFile,
  type RequestFile,
} from '@/shared/api/endpoints/requests';
import type { WorkflowType } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatDateTime } from '@/shared/lib/format';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/shared/ui/alert-dialog';
import { Button } from '@/shared/ui/button';
import { FileDropzone } from '@/shared/ui/file-dropzone';
import { ErrorPanel, LoadingPanel } from './StatePanels';

/**
 * Таб «Файлы» карточки заявки (ux.md §8.1, redesign.md §6.4): FileDropzone
 * (§3.9) + список строк — иконка типа, имя truncate, размер · автор · дата
 * мета-строкой и этап, в котором файл приложен (ФТ3 «файлы в статусах»: загрузка
 * отсюда — в текущий этап, из диалога перехода — к самому переходу). Форматы — 10
 * из ТЗ, как на сервере. Скачивание через backend (RBAC); загрузка и удаление своих —
 * право `requests:files`, удаление чужих — ещё и `requests:write_any`.
 */

export function requestFileIcon(fileName: string): LucideIcon {
  const ext = fileName.split('.').pop()?.toLowerCase() ?? '';
  if (['png', 'jpg', 'jpeg'].includes(ext)) return ImageIcon;
  if (['xlsx', 'xls'].includes(ext)) return SheetIcon;
  if (['zip', 'gz', 'gzip', 'tgz', 'rar'].includes(ext)) return Archive;
  return FileText;
}

export function RequestFiles({
  requestId,
  workflowType,
}: {
  requestId: string;
  /** Процесс заявки: право `requests:files` действует в нём (F2). */
  workflowType: WorkflowType;
}): ReactNode {
  const queryClient = useQueryClient();
  const { user, hasPermission } = useAuth();
  // Загрузка — право requests:files в процессе заявки; удаление — своих файлов
  // или любых при праве на чужие заявки (requests:write_any).
  const canEdit = hasPermission('requests:files', workflowType);
  const canDelete = (file: RequestFile): boolean =>
    canEdit &&
    (hasPermission('requests:write_any', workflowType) ||
      (Boolean(user) && file.uploaded_by === user?.id));

  const filesQuery = useQuery({
    queryKey: ['request-files', requestId],
    queryFn: ({ signal }) => listRequestFiles(requestId, signal),
  });
  const invalidate = (): Promise<void> =>
    queryClient.invalidateQueries({ queryKey: ['request-files', requestId] });

  // Прогресс: у API нет колбэка процентов — плавный ramp до 90, 100 по факту
  const [uploading, setUploading] = useState<{ name: string; percent: number } | null>(null);
  const rampRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopRamp = (): void => {
    if (rampRef.current) clearInterval(rampRef.current);
    rampRef.current = null;
  };

  const uploadFiles = async (files: File[]): Promise<void> => {
    for (const file of files) {
      setUploading({ name: file.name, percent: 8 });
      rampRef.current = setInterval(() => {
        setUploading((prev) =>
          prev ? { ...prev, percent: Math.min(prev.percent + 7, 90) } : prev,
        );
      }, 250);
      try {
        const uploaded = await uploadRequestFile(requestId, file);
        stopRamp();
        setUploading({ name: file.name, percent: 100 });
        toastSuccess(
          uploaded.stage_name
            ? `Файл «${file.name}» приложен к этапу «${uploaded.stage_name}»`
            : `Файл «${file.name}» загружен`,
        );
        await invalidate();
        void queryClient.invalidateQueries({ queryKey: ['request-history', requestId] });
      } catch (error) {
        toastError(error, { title: `Не удалось загрузить «${file.name}»` });
      } finally {
        stopRamp();
        setUploading(null);
      }
    }
  };

  const deleteMutation = useMutation({
    mutationFn: (fileId: string) => deleteRequestFile(requestId, fileId),
    onSuccess: () => {
      void invalidate();
      void queryClient.invalidateQueries({ queryKey: ['request-history', requestId] });
    },
  });

  const download = async (file: RequestFile): Promise<void> => {
    try {
      await downloadFile(file.id, file.file_name);
    } catch (error) {
      toastError(error, { title: 'Не удалось скачать файл' });
    }
  };

  if (filesQuery.isLoading) return <LoadingPanel rows={3} />;
  if (filesQuery.isError) {
    return (
      <ErrorPanel
        error={filesQuery.error}
        title="Не удалось загрузить список файлов"
        onRetry={() => void filesQuery.refetch()}
      />
    );
  }

  const files = filesQuery.data?.items ?? [];

  return (
    <div className="space-y-4">
      {canEdit ? (
        <FileDropzone
          accept={REQUEST_FILE_ACCEPT}
          maxSizeMb={REQUEST_FILE_MAX_MB}
          multiple
          onFiles={(picked) => void uploadFiles(picked)}
          hint={`${REQUEST_FILE_FORMATS_LABEL} · до ${REQUEST_FILE_MAX_MB} МБ · файл попадёт в текущий этап`}
          uploading={uploading}
        />
      ) : null}

      {files.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {canEdit ? 'Файлов пока нет — перетащите документы сюда' : 'Файлов пока нет'}
        </p>
      ) : (
        <ul className="m-0 list-none divide-y divide-border p-0">
          {files.map((file) => {
            const Icon = requestFileIcon(file.file_name);
            return (
              <li key={file.id} className="flex items-center gap-3 py-2.5">
                <span
                  aria-hidden="true"
                  className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary-tint text-primary"
                >
                  <Icon className="size-4" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{file.file_name}</div>
                  <div className="truncate text-xs text-muted-foreground">
                    {formatFileSize(file.size_bytes)}
                    {file.uploaded_by_name ? <> · {file.uploaded_by_name}</> : null}
                    {' · '}
                    {formatDateTime(file.created_at)}
                  </div>
                  {file.stage_name ? (
                    <div className="truncate text-xs text-muted-foreground">
                      этап «{file.stage_name}»
                    </div>
                  ) : null}
                </div>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={`Скачать «${file.file_name}»`}
                  onClick={() => void download(file)}
                >
                  <Download aria-hidden="true" />
                </Button>
                {canDelete(file) ? (
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="text-muted-foreground hover:text-status-danger-deep"
                        aria-label={`Удалить «${file.file_name}»`}
                      >
                        <Trash2 aria-hidden="true" />
                      </Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Удалить файл «{file.file_name}»?</AlertDialogTitle>
                        <AlertDialogDescription>
                          Файл станет недоступен всем участникам заявки.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Отмена</AlertDialogCancel>
                        <AlertDialogAction
                          className="bg-destructive text-destructive-foreground hover:bg-status-danger-deep"
                          onClick={() => deleteMutation.mutate(file.id)}
                        >
                          Удалить
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
