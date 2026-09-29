import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronLeft, ChevronRight, FilePlus2, Info, Loader2, Search, Table2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  importDictionaryJson,
  listDictionaries,
  listDictionaryItems,
} from '@/shared/api/endpoints/admin';
import { notifyApiError, notifySuccess } from '@/shared/api/feedback';
import type { DictionaryInfo } from '@/shared/api/types';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import { Button } from '@/shared/ui/button';
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from '@/shared/ui/data-table';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from '@/shared/ui/sheet';
import { Skeleton } from '@/shared/ui/skeleton';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/shared/ui/tabs';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Справочники и предзагрузка данных (ux.md §14.2, contract §10.1) на новом
 * ките: разовая загрузка из открытых источников — JSON напрямую или таблицы
 * через общий мастер импорта (`entity_type=dictionary:<name>`). Приложение
 * не обращается в интернет в рантайме — это проговаривается прямо в UI.
 */

const JSON_PLACEHOLDER = `[
  {"name": "Центральный федеральный округ", "code": "ЦФО"},
  {"name": "Северо-Западный федеральный округ", "code": "СЗФО"}
]`;

function parseItems(raw: string): unknown[] {
  const data: unknown = JSON.parse(raw);
  if (Array.isArray(data)) return data;
  if (typeof data === 'object' && data !== null && Array.isArray((data as { items?: unknown }).items)) {
    return (data as { items: unknown[] }).items;
  }
  throw new Error('Ожидается JSON-массив объектов или {"items": [...]}');
}

interface UploadModalProps {
  dictionary: DictionaryInfo | null;
  onClose: () => void;
}

function UploadModal({ dictionary, onClose }: UploadModalProps): ReactNode {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [jsonText, setJsonText] = useState('');
  const [strategy, setStrategy] = useState<'upsert' | 'create_only'>('upsert');
  const [parseError, setParseError] = useState<string | null>(null);

  const importMutation = useMutation({
    mutationFn: ({ name, items }: { name: string; items: unknown[] }) =>
      importDictionaryJson(name, items, strategy),
    meta: { silent: true },
    onSuccess: (result) => {
      notifySuccess(
        'Справочник загружен',
        `Создано: ${result.created}, обновлено: ${result.updated}, с ошибками: ${result.failed}`,
      );
      void queryClient.invalidateQueries({ queryKey: ['admin-dictionaries'] });
      setJsonText('');
      onClose();
    },
    onError: (error) => notifyApiError(error, 'Загрузка справочника не удалась'),
  });

  const submitJson = (): void => {
    if (!dictionary) return;
    setParseError(null);
    let items: unknown[];
    try {
      items = parseItems(jsonText);
    } catch (error) {
      setParseError(error instanceof Error ? error.message : 'Некорректный JSON');
      return;
    }
    if (items.length === 0) {
      setParseError('Массив пуст — нечего загружать');
      return;
    }
    importMutation.mutate({ name: dictionary.name, items });
  };

  return (
    <Dialog open={Boolean(dictionary)} onOpenChange={(next) => { if (!next) onClose(); }}>
      <DialogContent className="sm:max-w-[640px]" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>{dictionary ? `Загрузить данные: ${dictionary.label}` : ''}</DialogTitle>
        </DialogHeader>
        <Tabs defaultValue="json">
          <TabsList>
            <TabsTrigger value="json">
              <FilePlus2 className="size-4" aria-hidden="true" />
              JSON напрямую
            </TabsTrigger>
            <TabsTrigger value="table">
              <Table2 className="size-4" aria-hidden="true" />
              Таблица (XLSX/XLS/CSV)
            </TabsTrigger>
          </TabsList>
          <TabsContent value="json" className="grid gap-3 pt-3">
            <Textarea
              rows={10}
              value={jsonText}
              onChange={(e) => setJsonText(e.target.value)}
              placeholder={JSON_PLACEHOLDER}
              spellCheck={false}
              className="font-mono text-xs"
              aria-label="JSON справочника"
            />
            {parseError ? (
              <p className="text-xs text-status-danger-deep" role="alert">
                {parseError}
              </p>
            ) : null}
            <RadioGroup
              value={strategy}
              onValueChange={(value) => setStrategy(value as 'upsert' | 'create_only')}
              className="grid gap-2"
            >
              <Label className="flex items-center gap-2 text-sm font-normal">
                <RadioGroupItem value="upsert" />
                Обновлять существующие (upsert)
              </Label>
              <Label className="flex items-center gap-2 text-sm font-normal">
                <RadioGroupItem value="create_only" />
                Только создавать новые
              </Label>
            </RadioGroup>
            <div>
              <Button onClick={submitJson} disabled={!jsonText.trim() || importMutation.isPending}>
                {importMutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                {importMutation.isPending ? 'Загружаем…' : 'Загрузить JSON'}
              </Button>
            </div>
          </TabsContent>
          <TabsContent value="table" className="grid gap-3 pt-3">
            <p className="text-sm text-muted-foreground">
              Табличные файлы загружаются через общий мастер импорта: тот же диалог маппинга
              колонок, исправление кодировок и построчный отчёт об ошибках.
            </p>
            <div>
              <Button
                onClick={() => {
                  if (dictionary) navigate(`/import?entity=dictionary:${dictionary.name}`);
                }}
              >
                Открыть мастер импорта
              </Button>
            </div>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

interface ItemsDrawerProps {
  dictionary: DictionaryInfo | null;
  onClose: () => void;
}

function ItemsDrawer({ dictionary, onClose }: ItemsDrawerProps): ReactNode {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const debounced = useDebouncedValue(search, 400);
  const pageSize = 20;

  const itemsQuery = useQuery({
    queryKey: ['dictionary-items', dictionary?.name, debounced, page],
    queryFn: ({ signal }) =>
      listDictionaryItems(dictionary?.name as string, {
        limit: pageSize,
        offset: (page - 1) * pageSize,
        search: debounced.trim() || undefined,
        signal,
      }),
    enabled: Boolean(dictionary),
  });

  const total = itemsQuery.data?.total ?? 0;
  const maxPage = Math.max(1, Math.ceil(total / pageSize));

  return (
    <Sheet open={Boolean(dictionary)} onOpenChange={(next) => { if (!next) onClose(); }}>
      <SheetContent side="right" className="w-full gap-0 overflow-y-auto sm:max-w-[520px]">
        <SheetHeader>
          <SheetTitle>{dictionary?.label}</SheetTitle>
        </SheetHeader>
        <div className="grid gap-3 px-4 pb-4">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              placeholder="Поиск по записям"
              className="pl-8"
              aria-label="Поиск по записям справочника"
            />
          </div>
          {itemsQuery.isError ? (
            <ErrorState error={itemsQuery.error} onRetry={() => void itemsQuery.refetch()} />
          ) : itemsQuery.isLoading ? (
            <div className="grid gap-2">
              <Skeleton className="h-9" />
              <Skeleton className="h-9" />
              <Skeleton className="h-9" />
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Название</TableHead>
                    <TableHead scope="col">Код</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(itemsQuery.data?.items ?? []).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={2} className="py-6 text-center text-muted-foreground">
                        Записей нет
                      </TableCell>
                    </TableRow>
                  ) : (
                    (itemsQuery.data?.items ?? []).map((item) => (
                      <TableRow key={item.id}>
                        <TableCell>{item.name}</TableCell>
                        <TableCell>{item.code ? String(item.code) : '—'}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              {total > pageSize ? (
                <div className="flex items-center justify-between text-sm text-muted-foreground">
                  <span className="tabular">
                    {(page - 1) * pageSize + 1}–{Math.min(page * pageSize, total)} из {total}
                  </span>
                  <span className="flex gap-1">
                    <Button
                      variant="outline"
                      size="icon"
                      aria-label="Предыдущая страница"
                      disabled={page <= 1}
                      onClick={() => setPage((p) => Math.max(1, p - 1))}
                    >
                      <ChevronLeft aria-hidden="true" />
                    </Button>
                    <Button
                      variant="outline"
                      size="icon"
                      aria-label="Следующая страница"
                      disabled={page >= maxPage}
                      onClick={() => setPage((p) => Math.min(maxPage, p + 1))}
                    >
                      <ChevronRight aria-hidden="true" />
                    </Button>
                  </span>
                </div>
              ) : null}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function AdminDictionariesPage(): ReactNode {
  const [uploadTarget, setUploadTarget] = useState<DictionaryInfo | null>(null);
  const [viewTarget, setViewTarget] = useState<DictionaryInfo | null>(null);

  const dictionariesQuery = useQuery({
    queryKey: ['admin-dictionaries'],
    queryFn: ({ signal }) => listDictionaries(signal),
  });

  let body: ReactNode;
  if (dictionariesQuery.isLoading) {
    body = <LoadingState rows={6} card={false} />;
  } else if (dictionariesQuery.isError) {
    body = (
      <ErrorState
        error={dictionariesQuery.error}
        onRetry={() => void dictionariesQuery.refetch()}
        title="Не удалось загрузить справочники"
      />
    );
  } else if ((dictionariesQuery.data ?? []).length === 0) {
    body = (
      <EmptyState
        illustration="registry-empty"
        title="Справочников нет"
        description="Backend ещё не объявил ни одного справочника"
      />
    );
  } else {
    body = (
      <ul className="grid gap-2">
        {(dictionariesQuery.data ?? []).map((dict) => (
          <li
            key={dict.name}
            className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-border bg-card px-4 py-3 shadow-card"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{dict.label}</p>
              <p className="truncate font-mono text-xs text-muted-foreground">{dict.name}</p>
            </div>
            <span className="tabular text-sm text-muted-foreground">
              записей: {dict.items_count}
            </span>
            <span className="flex gap-2">
              <Button variant="outline" size="sm" onClick={() => setViewTarget(dict)}>
                Просмотреть
              </Button>
              <Button size="sm" onClick={() => setUploadTarget(dict)}>
                Загрузить данные…
              </Button>
            </span>
          </li>
        ))}
      </ul>
    );
  }

  return (
    <>
      <PageHeader title="Справочники" subtitle="Разовая предзагрузка из открытых источников" />
      <div className="mb-4 flex items-start gap-2 rounded-lg border border-border bg-primary-tint px-4 py-3 text-sm">
        <Info className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" />
        <p>
          Данные загружаются вручную (JSON или таблицы через мастер импорта). Приложение не
          обращается в интернет в рантайме — закрытый контур.
        </p>
      </div>
      {body}
      <UploadModal dictionary={uploadTarget} onClose={() => setUploadTarget(null)} />
      <ItemsDrawer dictionary={viewTarget} onClose={() => setViewTarget(null)} />
    </>
  );
}
