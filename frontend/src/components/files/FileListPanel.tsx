import { Download, File as FileIcon, Loader2, Trash2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { downloadFile, formatFileSize } from '@/shared/api/endpoints/files';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type { FileObject } from '@/shared/api/types';
import { formatDateTime } from '@/shared/lib/format';
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
import { Skeleton } from '@/shared/ui/skeleton';
import { toastError } from '@/shared/lib/toast';

/**
 * Панель файлов сущности из MinIO (ux.md §8.1 «Файлы») на новом ките:
 * FileDropzone §3.9 с whitelist расширений и лимитом 50 МБ, строки файлов
 * с размером/датой, скачивание через backend (`GET /files/{id}/download`)
 * и мягкое удаление через AlertDialog.
 */

const DEFAULT_ACCEPT = ['.pdf', '.docx', '.xlsx', '.png', '.jpg', '.jpeg'];
const MAX_SIZE_MB = 50;

interface FileListPanelProps {
  files: FileObject[];
  loading?: boolean;
  /** null — загрузка недоступна (роль/режим просмотра). */
  onUpload?: ((file: File) => Promise<unknown>) | null;
  onDelete?: ((fileId: string) => Promise<unknown>) | null;
  emptyText?: string;
}

export function FileListPanel({
  files,
  loading = false,
  onUpload,
  onDelete,
  emptyText = 'Файлов пока нет',
}: FileListPanelProps): ReactNode {
  const [uploadingName, setUploadingName] = useState<string | null>(null);

  const handleFiles = async (accepted: File[]): Promise<void> => {
    if (!onUpload) return;
    for (const file of accepted) {
      setUploadingName(file.name);
      try {
        await onUpload(file);
        notifySuccess(`Файл «${file.name}» загружен`);
      } catch (error) {
        notifyApiError(error, 'Не удалось загрузить файл');
      } finally {
        setUploadingName(null);
      }
    }
  };

  const handleDownload = async (file: FileObject): Promise<void> => {
    try {
      await downloadFile(file.id, file.file_name);
    } catch (error) {
      notifyApiError(error, 'Не удалось скачать файл');
    }
  };

  return (
    <div className="grid gap-4">
      {onUpload ? (
        <FileDropzone
          accept={DEFAULT_ACCEPT}
          maxSizeMb={MAX_SIZE_MB}
          multiple
          onFiles={(accepted) => void handleFiles(accepted)}
          hint={`PDF, DOCX, XLSX, PNG, JPG — до ${MAX_SIZE_MB} МБ`}
          uploading={uploadingName ? { name: uploadingName, percent: 100 } : null}
        />
      ) : null}

      {loading ? (
        <div className="grid gap-2">
          <Skeleton className="h-12" />
          <Skeleton className="h-12" />
        </div>
      ) : files.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted-foreground">{emptyText}</p>
      ) : (
        <ul className="divide-y divide-border">
          {files.map((file) => (
            <li key={file.id} className="flex items-center gap-3 py-2.5">
              <FileIcon className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{file.file_name}</p>
                <p className="text-xs text-muted-foreground">
                  {formatFileSize(file.size_bytes)} · {formatDateTime(file.created_at)}
                  {file.uploaded_by_name ? ` · ${file.uploaded_by_name}` : ''}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Скачать ${file.file_name}`}
                onClick={() => void handleDownload(file)}
              >
                {uploadingName === file.file_name ? (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                ) : (
                  <Download aria-hidden="true" />
                )}
              </Button>
              {onDelete ? (
                <AlertDialog>
                  <AlertDialogTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Удалить ${file.file_name}`}
                      className="text-status-danger-deep hover:text-status-danger-deep"
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>Удалить файл?</AlertDialogTitle>
                      <AlertDialogDescription>
                        «{file.file_name}» будет удалён из карточки.
                      </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>Отмена</AlertDialogCancel>
                      <AlertDialogAction
                        className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        onClick={() => {
                          onDelete(file.id).catch((error: unknown) => {
                            toastError(error, { title: 'Не удалось удалить файл' });
                          });
                        }}
                      >
                        Удалить
                      </AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
