import { ArrowLeft, ArrowRight, Info, Loader2, TriangleAlert } from 'lucide-react';
import type { ReactNode } from 'react';
import { formatFileSize } from '@/shared/api/endpoints/files';
import type { ImportSession } from '@/shared/api/types';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { ENCODING_OPTIONS, type PreviewTable } from './importModel';

/**
 * Шаг 2 мастера импорта (redesign.md §6.6): превью «как распарсили», селекты
 * листа и кодировки, признак заголовков. Для CSV смена кодировки — мгновенная
 * перерисовка (файл декодируется в браузере), warning-alert при «кракозябрах»;
 * сервер синхронизируется PUT'ом опций.
 */

interface PreviewStepProps {
  session: ImportSession;
  preview: PreviewTable | null;
  mojibake: boolean;
  isCsv: boolean;
  sheet: number;
  headerRow: boolean;
  encoding: string | null;
  refreshing: boolean;
  onChangeSheet: (sheet: number) => void;
  onChangeHeaderRow: (checked: boolean) => void;
  onChangeEncoding: (encoding: string | null) => void;
  onBack: () => void;
  onNext: () => void;
}

export function PreviewStep({
  session,
  preview,
  mojibake,
  isCsv,
  sheet,
  headerRow,
  encoding,
  refreshing,
  onChangeSheet,
  onChangeHeaderRow,
  onChangeEncoding,
  onBack,
  onNext,
}: PreviewStepProps): ReactNode {
  const sheets = session.detected?.sheets ?? [];
  const detectedEncoding = session.detected?.encoding;
  const header = preview?.header ?? [];

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {session.file?.file_name}
        {session.file?.size_bytes ? ` · ${formatFileSize(session.file.size_bytes)}` : ''}
        {detectedEncoding ? ` · кодировка определена как ${detectedEncoding}` : ''}
      </p>
      {mojibake ? (
        <Alert variant="warning">
          <TriangleAlert aria-hidden="true" />
          <AlertTitle>Похоже, кодировка определена неверно — в тексте «кракозябры»</AlertTitle>
          <AlertDescription>
            Выберите кодировку вручную (обычно помогает Windows-1251) — превью перерисуется
            мгновенно.
          </AlertDescription>
        </Alert>
      ) : null}
      <div className="flex flex-wrap items-center gap-4">
        {sheets.length > 1 ? (
          <div className="flex items-center gap-2">
            <span className="text-sm">Лист:</span>
            <Select value={String(sheet)} onValueChange={(value) => onChangeSheet(Number(value))}>
              <SelectTrigger className="w-[220px]" aria-label="Лист файла">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sheets.map((s) => (
                  <SelectItem key={s.index} value={String(s.index)}>
                    {s.name} ({s.rows} строк)
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={headerRow}
            onCheckedChange={(checked) => onChangeHeaderRow(checked === true)}
          />
          Первая строка — заголовки
        </label>
        {isCsv ? (
          <div className="flex items-center gap-2">
            <span className="text-sm">Кодировка:</span>
            <Select
              value={encoding ?? '__auto__'}
              onValueChange={(value) => onChangeEncoding(value === '__auto__' ? null : value)}
            >
              <SelectTrigger className="w-[190px]" aria-label="Кодировка файла">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ENCODING_OPTIONS.map((option) => (
                  <SelectItem key={option.value ?? '__auto__'} value={option.value ?? '__auto__'}>
                    {option.value === null && detectedEncoding
                      ? `Авто (${detectedEncoding})`
                      : option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        ) : null}
        {refreshing ? (
          <Loader2 className="size-4 animate-spin text-muted-foreground" aria-hidden="true" />
        ) : null}
      </div>

      {preview && preview.rows.length > 0 ? (
        <div className="max-h-[380px] overflow-auto rounded-md border">
          <Table>
            <TableHeader className="sticky top-0 z-[1] bg-muted">
              <TableRow>
                {header.map((name, index) => (
                  <TableHead
                    key={index}
                    scope="col"
                    className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    {name || `Колонка ${index + 1}`}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {preview.rows.map((row, rowIndex) => (
                <TableRow key={rowIndex}>
                  {header.map((_name, cellIndex) => (
                    <TableCell
                      key={cellIndex}
                      className="max-w-[220px] truncate whitespace-nowrap"
                      title={row[cellIndex]}
                    >
                      {row[cellIndex] ?? ''}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      ) : (
        <Alert variant="info">
          <Info aria-hidden="true" />
          <AlertTitle>Превью недоступно</AlertTitle>
          <AlertDescription>
            Сервер не вернул строки превью. Продолжите — колонки для маппинга распознаны.
          </AlertDescription>
        </Alert>
      )}

      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={onBack}>
          <ArrowLeft aria-hidden="true" /> Загрузить другой файл
        </Button>
        <Button onClick={onNext}>
          К маппингу колонок <ArrowRight aria-hidden="true" />
        </Button>
      </div>
    </div>
  );
}
