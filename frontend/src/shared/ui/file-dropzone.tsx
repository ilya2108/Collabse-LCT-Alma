import { FileUp } from 'lucide-react';
import { useId, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { Illustration, type IllustrationName } from '@/shared/illustrations';
import { Progress } from '@/shared/ui/progress';
import { cn } from '@/shared/lib/cn';

/**
 * FileDropzone (redesign.md §3.9, замена Upload.Dragger): свой DnD c
 * dragover-состоянием border-primary bg-primary-tint, скрытый <input
 * type="file"> с label, ошибки формата/размера — inline
 * text-status-danger-deep (не toast). Контракт заморожен (§9) — его
 * импортируют WP4 (файлы заявки) и WP5 (мастер импорта).
 */

/** Сюжет иллюстрации §4 ('import-drop' в мастере) — общий реестр WP6. */
export type { IllustrationName };

export interface FileDropzoneProps {
  /** ['.xlsx','.xls','.csv','.json'] / ['.pdf','.docx',…] */
  accept: string[];
  maxSizeMb: number;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
  illustration?: IllustrationName;
  /** «XLSX, XLS, CSV или JSON · до 20 МБ» (пробелы неразрывные). */
  hint?: string;
  /** Прогресс текущей загрузки. */
  uploading?: { name: string; percent: number } | null;
}

export function FileDropzone({
  accept,
  maxSizeMb,
  multiple = false,
  onFiles,
  illustration,
  hint,
  uploading = null,
}: FileDropzoneProps): ReactNode {
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  const acceptNormalized = accept.map((ext) => ext.toLowerCase());

  const takeFiles = (list: FileList | File[]): void => {
    const files = Array.from(list);
    const picked = multiple ? files : files.slice(0, 1);
    const good: File[] = [];
    const problems: string[] = [];
    for (const file of picked) {
      const ext = `.${file.name.split('.').pop()?.toLowerCase() ?? ''}`;
      if (!acceptNormalized.includes(ext)) {
        problems.push(`«${file.name}»: формат ${ext} не поддерживается`);
        continue;
      }
      if (file.size > maxSizeMb * 1024 * 1024) {
        problems.push(`«${file.name}»: больше ${maxSizeMb} МБ`);
        continue;
      }
      good.push(file);
    }
    setErrors(problems);
    if (good.length > 0) onFiles(good);
  };

  const handleDrop = (e: DragEvent<HTMLElement>): void => {
    e.preventDefault();
    setDragOver(false);
    if (uploading) return;
    takeFiles(e.dataTransfer.files);
  };

  return (
    <div>
      <label
        htmlFor={inputId}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        className={cn(
          'flex min-h-28 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-6 text-center',
          'transition-[border-color,background-color] duration-150',
          dragOver ? 'border-primary bg-primary-tint' : 'border-border bg-card hover:border-primary/50',
          uploading && 'pointer-events-none opacity-70',
        )}
      >
        <input
          ref={inputRef}
          id={inputId}
          type="file"
          className="sr-only"
          accept={accept.join(',')}
          multiple={multiple}
          disabled={Boolean(uploading)}
          onChange={(e) => {
            if (e.target.files) takeFiles(e.target.files);
            e.target.value = '';
          }}
        />
        {illustration ? (
          <Illustration name={illustration} height={112} className="mx-auto" />
        ) : (
          <span
            aria-hidden="true"
            className="flex size-10 items-center justify-center rounded-md bg-primary-tint text-primary"
          >
            <FileUp className="size-5" />
          </span>
        )}
        <span className="text-sm font-medium text-foreground">
          Перетащите файл{multiple ? 'ы' : ''} сюда или нажмите, чтобы выбрать
        </span>
        {hint ? <span className="text-xs text-muted-foreground">{hint}</span> : null}
      </label>

      {uploading ? (
        <div className="mt-3" aria-live="polite">
          <div className="mb-1 flex items-center justify-between gap-3 text-xs text-muted-foreground">
            <span className="min-w-0 truncate">Загружаем «{uploading.name}»…</span>
            <span className="tabular shrink-0">{Math.round(uploading.percent)}%</span>
          </div>
          <Progress value={uploading.percent} aria-label={`Загрузка «${uploading.name}»`} />
        </div>
      ) : null}

      {errors.length > 0 ? (
        <ul className="mt-2 list-none space-y-0.5 p-0 text-xs text-status-danger-deep" role="alert">
          {errors.map((message) => (
            <li key={message}>{message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
