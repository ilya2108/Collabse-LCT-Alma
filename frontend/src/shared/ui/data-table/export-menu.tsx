import { Download, Loader2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import {
  runExportAndDownload,
  type CreateExportJobPayload,
} from '@/shared/api/endpoints/exportJobs';
import type { ExportFormat } from '@/shared/api/types';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';

/**
 * Кнопка «Экспорт» нового кита (ux.md §4.2): задание /export/jobs с текущими
 * фильтрами экрана, поллинг готовности и скачивание. Замена AntD ExportMenu
 * в мигрированных экранах WP2.
 */

const FORMAT_LABELS: Record<ExportFormat, string> = {
  xlsx: 'XLSX',
  csv: 'CSV (ru-Excel, «;»)',
  json: 'JSON',
  pdf: 'PDF отчёт',
};

type CsvEncoding = NonNullable<NonNullable<CreateExportJobPayload['options']>['encoding']>;

const CSV_ENCODING_ITEMS: { encoding: CsvEncoding; label: string }[] = [
  { encoding: 'utf-8', label: 'CSV — utf-8 (BOM), «;»' },
  { encoding: 'cp1251', label: 'CSV — cp1251 (win-Excel), «;»' },
];

export interface ExportMenuProps {
  entityType: string;
  filters?: () => Record<string, unknown>;
  columns?: string[];
  formats?: ExportFormat[];
  fileName?: string;
  buttonText?: string;
}

export function ExportMenu({
  entityType,
  filters,
  columns,
  formats = ['xlsx', 'csv'],
  fileName,
  buttonText = 'Экспорт',
}: ExportMenuProps): ReactNode {
  const [running, setRunning] = useState(false);

  const runExport = async (format: ExportFormat, encoding: CsvEncoding = 'utf-8'): Promise<void> => {
    setRunning(true);
    try {
      const name = await runExportAndDownload(
        {
          entity_type: entityType,
          format,
          filters: filters?.(),
          columns,
          options: format === 'csv' ? { encoding, csv_delimiter: ';' } : undefined,
        },
        fileName ?? entityType.replace(':', '-'),
      );
      toastSuccess('Файл выгружен', name);
    } catch (error) {
      toastError(error, { title: 'Не удалось выгрузить данные' });
    } finally {
      setRunning(false);
    }
  };

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" disabled={running}>
          {running ? (
            <Loader2 className="animate-spin" aria-hidden="true" />
          ) : (
            <Download aria-hidden="true" />
          )}
          {buttonText}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {formats.flatMap((format) =>
          format === 'csv'
            ? CSV_ENCODING_ITEMS.map(({ encoding, label }) => (
                <DropdownMenuItem key={`csv-${encoding}`} onSelect={() => void runExport('csv', encoding)}>
                  {label}
                </DropdownMenuItem>
              ))
            : [
                <DropdownMenuItem key={format} onSelect={() => void runExport(format)}>
                  {FORMAT_LABELS[format]}
                </DropdownMenuItem>,
              ],
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
