import { useState, type ReactNode } from 'react';
import { FileDropzone } from '@/components/wp5/FileDropzone';
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
  entityLabel,
  isDictionaryEntity,
} from './importModel';

/**
 * Шаг 1 мастера импорта (redesign.md §6.6): дропзона FileDropzone (§3.9)
 * во всю ширину + выбор целевой сущности. Ошибки формата/размера — inline
 * в дропзоне, не toast. Файл сразу уходит на сервер для распознавания.
 */

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

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-sm font-medium">Что импортируем:</span>
        <Select value={entity} onValueChange={onEntityChange} disabled={uploading}>
          <SelectTrigger className="w-[280px]" aria-label="Целевая сущность импорта">
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
