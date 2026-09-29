import { useQuery } from '@tanstack/react-query';
import { Building2, FileText, GraduationCap, Search, ScrollText } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/shared/api/client';
import { cn } from '@/shared/lib/cn';
import { useDebouncedValue } from '@/shared/lib/useDebouncedValue';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/shared/ui/command';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';

/**
 * SearchCommand — глобальный поиск Cmd+K (redesign.md §3.10, §6.2):
 * cmdk-палитра с группами Вузы/Заявки/Студенты/Договоры через
 * `GET /api/v1/search?q=`. Без пропсов; рендерит кнопку-триггер «Поиск… ⌘K»
 * и сам диалог. Контракт — API-заморозка (§9).
 */

interface SearchResponse {
  universities: Array<{ id: string; name: string; short_name: string | null; city: string | null }>;
  requests: Array<{
    id: string;
    title: string;
    workflow_type: string;
    status: { id: string; name: string; color: string | null };
  }>;
  students: Array<{ id: string; display_name: string; funnel_status: string | null }>;
  contracts: Array<{ id: string; number: string; status: string | null }>;
}

function globalSearch(q: string, signal?: AbortSignal): Promise<SearchResponse> {
  return api.get<SearchResponse>('/search', { query: { q }, signal });
}

function isMacLike(): boolean {
  return typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
}

export function SearchCommand(): ReactNode {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const debounced = useDebouncedValue(query.trim(), 300);

  // Глобальный хоткей Cmd+K / Ctrl+K (§3.10).
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setOpen((prev) => !prev);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const searchQuery = useQuery({
    queryKey: ['global-search', debounced],
    queryFn: ({ signal }) => globalSearch(debounced, signal),
    enabled: open && debounced.length > 0,
    staleTime: 15_000,
    placeholderData: (prev) => prev,
  });

  const data = debounced.length > 0 ? (searchQuery.data ?? null) : null;
  const total = data
    ? data.universities.length + data.requests.length + data.students.length + data.contracts.length
    : 0;

  const goTo = (path: string): void => {
    setOpen(false);
    setQuery('');
    navigate(path);
  };

  const shortcutLabel = useMemo(() => (isMacLike() ? '⌘ K' : 'Ctrl K'), []);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        data-tour="header-search"
        aria-label="Глобальный поиск"
        className={cn(
          'inline-flex h-9 items-center gap-2 rounded-md border border-input bg-card px-3 text-sm text-muted-foreground',
          'transition-[color,background-color,border-color,box-shadow] duration-150 hover:bg-accent hover:text-accent-foreground',
          'w-9 justify-center px-0 md:w-52 md:justify-start md:px-3 lg:w-64',
        )}
      >
        <Search className="size-4 shrink-0" aria-hidden="true" />
        <span className="hidden flex-1 truncate text-left md:inline">Поиск…</span>
        <kbd className="hidden rounded-sm border border-border bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground md:inline">
          {shortcutLabel}
        </kbd>
      </button>

      <Dialog open={open} onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery('');
      }}>
        {/* Паттерн командной палитры: диалог в верхней трети экрана (top-[15vh]),
            скругление не режет контент (overflow-hidden + gap-0/p-0 у контейнера),
            тень — shadow-overlay из базового DialogContent (токены §2.2). */}
        <DialogContent
          showCloseButton={false}
          className="top-[15vh] translate-y-0 gap-0 overflow-hidden p-0 sm:max-w-[560px]"
        >
          <DialogHeader className="sr-only">
            <DialogTitle>Глобальный поиск</DialogTitle>
            <DialogDescription>Вузы, заявки, студенты и договоры</DialogDescription>
          </DialogHeader>
          {/* Результаты приходят с сервера уже отфильтрованными — cmdk не фильтрует. */}
          {/* Поле ввода выше базового (h-9 < h-10 у input) — иначе верх среза́лся
              overflow-hidden контейнера; здесь обёртке задаётся h-12. */}
          <Command
            shouldFilter={false}
            className="[&_[cmdk-input-wrapper]]:h-12 [&_[cmdk-input-wrapper]]:px-4 [&_[cmdk-input]]:h-12"
          >
            <CommandInput
              value={query}
              onValueChange={setQuery}
              placeholder="Вуз, заявка, студент или договор…"
            />
            <div aria-live="polite" className="sr-only">
              {debounced.length > 0 && !searchQuery.isFetching ? `Найдено результатов: ${total}` : ''}
            </div>
            <CommandList className="max-h-[min(420px,55vh)] px-1.5 py-2">
              {debounced.length === 0 ? (
                <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                  Начните вводить запрос — найдём вуз, заявку, студента или договор
                </p>
              ) : searchQuery.isPending ? (
                <p className="px-4 py-8 text-center text-sm text-muted-foreground" aria-live="polite">
                  Ищем…
                </p>
              ) : searchQuery.isError ? (
                <p className="px-4 py-8 text-center text-sm text-status-danger-deep">
                  Поиск временно недоступен
                </p>
              ) : (
                <>
                  <CommandEmpty>Ничего не найдено. Уточните запрос</CommandEmpty>
                  {data && data.universities.length > 0 ? (
                    <CommandGroup heading="Вузы">
                      {data.universities.map((item) => (
                        <CommandItem
                          key={item.id}
                          value={`university-${item.id}`}
                          onSelect={() => goTo(`/registry/universities/${item.id}`)}
                        >
                          <Building2 className="text-muted-foreground" aria-hidden="true" />
                          <span className="min-w-0 flex-1 truncate">{item.name}</span>
                          {item.city ? (
                            <span className="text-xs text-muted-foreground">{item.city}</span>
                          ) : null}
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  ) : null}
                  {data && data.requests.length > 0 ? (
                    <CommandGroup heading="Заявки">
                      {data.requests.map((item) => (
                        <CommandItem
                          key={item.id}
                          value={`request-${item.id}`}
                          onSelect={() => goTo(`/requests/${item.id}`)}
                        >
                          <FileText className="text-muted-foreground" aria-hidden="true" />
                          <span className="min-w-0 flex-1 truncate">{item.title}</span>
                          <span className="text-xs text-muted-foreground">{item.status.name}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  ) : null}
                  {data && data.students.length > 0 ? (
                    <CommandGroup heading="Студенты">
                      {data.students.map((item) => (
                        <CommandItem
                          key={item.id}
                          value={`student-${item.id}`}
                          onSelect={() => goTo(`/talent-pool/students/${item.id}`)}
                        >
                          <GraduationCap className="text-muted-foreground" aria-hidden="true" />
                          <span className="min-w-0 flex-1 truncate">{item.display_name}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  ) : null}
                  {data && data.contracts.length > 0 ? (
                    <CommandGroup heading="Договоры">
                      {data.contracts.map((item) => (
                        <CommandItem
                          key={item.id}
                          value={`contract-${item.id}`}
                          onSelect={() => goTo(`/registry/contracts/${item.id}`)}
                        >
                          <ScrollText className="text-muted-foreground" aria-hidden="true" />
                          <span className="min-w-0 flex-1 truncate">{item.number}</span>
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  ) : null}
                </>
              )}
            </CommandList>
          </Command>
        </DialogContent>
      </Dialog>
    </>
  );
}
