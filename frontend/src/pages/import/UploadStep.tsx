import { Download, Info } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { FileDropzone } from '@/components/wp5/FileDropzone';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import {
  ACCEPTED_EXTENSIONS,
  ENTITY_OPTIONS,
  MAX_FILE_SIZE_MB,
  downloadTemplate,
  entityGuide,
  entityLabel,
  isDictionaryEntity,
  type EntityGuide,
} from './importModel';

/**
 * Шаг 1 мастера импорта (redesign.md §6.6): дропзона FileDropzone (§3.9)
 * во всю ширину + выбор целевой сущности. Ошибки формата/размера — inline
 * в дропзоне, не toast. Файл сразу уходит на сервер для распознавания.
 */

/** Памятка составного импорта: какие колонки ждёт сервер и шаблон файла. */
function EntityGuidePanel({ guide }: { guide: EntityGuide }): ReactNode {
  return (
    <Alert variant="info">
      <Info aria-hidden="true" />
      <AlertTitle>Какие колонки нужны</AlertTitle>
      <AlertDescription>
        <p>{guide.summary}</p>
        <ul className="mt-2 grid gap-x-6 gap-y-1 sm:grid-cols-2">
          {guide.columns.map((column) => (
            <li key={column.name} className="min-w-0 text-sm">
              <span className="font-medium">
                {column.name}
                {column.required ? (
                  <span className="text-status-danger-deep" aria-label="обязательная колонка">
                    {' '}
                    *
                  </span>
                ) : null}
              </span>
              <span className="text-muted-foreground"> — {column.hint}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm">
          Названия колонок могут отличаться: мастер узнаёт синонимы, а сопоставление можно
          поправить на шаге маппинга. Лишние колонки не мешают.
        </p>
        <Button
          variant="outline"
          size="sm"
          className="mt-3"
          onClick={() => downloadTemplate(guide)}
        >
          <Download aria-hidden="true" /> Скачать шаблон CSV
        </Button>
      </AlertDescription>
    </Alert>
  );
}

interface UploadStepProps {
  entity: string;
  onEntityChange: (value: string) => void;
  uploading: boolean;
  onStart: (file: File) => void;
}

export function UploadStep({
  entity,
  onEntityChange,
  uploading,
  onStart,
}: UploadStepProps): ReactNode {
  const [pendingName, setPendingName] = useState('');
  const guide = entityGuide(entity);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Что импортируем:</span>
        <Select value={entity} onValueChange={onEntityChange} disabled={uploading}>
          <SelectTrigger className="w-full sm:w-[340px]" aria-label="Целевая сущность импорта">
            <SelectValue placeholder="Выберите сущность…" />
          </SelectTrigger>
          <SelectContent>
            {ENTITY_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
            {/* справочник админки приходит из /admin/dictionaries — добавляем опцию */}
            {isDictionaryEntity(entity) ? (
              <SelectItem value={entity}>{entityLabel(entity)}</SelectItem>
            ) : null}
          </SelectContent>
        </Select>
      </div>
      {guide ? <EntityGuidePanel guide={guide} /> : null}
      <FileDropzone
        accept={ACCEPTED_EXTENSIONS.map((ext) => `.${ext}`)}
        maxSizeMb={MAX_FILE_SIZE_MB}
        illustration="import-drop"
        hint={`XLSX, XLS, CSV или JSON · до ${MAX_FILE_SIZE_MB} МБ. Старый Excel (.xls) и «битые» кодировки CSV поддерживаются — кодировку можно поправить на следующем шаге.`}
        uploading={uploading ? { name: pendingName, percent: 100 } : null}
        onFiles={(files) => {
          const file = files[0];
          if (!file) return;
          setPendingName(file.name);
          onStart(file);
        }}
      />
    </div>
  );
}
