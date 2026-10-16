import { ChevronDown, ListTree, Printer, Search, X } from 'lucide-react';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { canSeeAdminGuide } from '@/app/menuConfig';
import { useAuth } from '@/shared/auth/AuthContext';
import { cn } from '@/shared/lib/cn';
import { Button } from '@/shared/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/shared/ui/collapsible';
import { EmptyState, PageHeader, SegmentedControl } from '@/shared/ui/data-table';
import { Input } from '@/shared/ui/input';
import { HelpBlocks } from './HelpBlocks';
import { HELP_GUIDES, blockText, sectionsFor, type HelpGuide, type HelpSection } from './content';

/**
 * Встроенная справка (НФТ «документация встроена в платформу»): руководство
 * пользователя — /help, руководство администратора — /help/admin (права
 * администратора, guard в роутере). Текст — из content/*.ts, тот же источник
 * собирает PDF в docs/manual. У каждого раздела свой якорь (/help#board) —
 * ссылку можно дать из любого экрана.
 *
 * Вёрстка: < lg — оглавление сворачивается над текстом, ≥ lg — липкая колонка слева.
 * Печать (Ctrl+P или кнопка) убирает меню, шапку и оглавление — получается PDF руководства.
 */

const GUIDE_PATHS: Record<HelpGuide['id'], string> = { user: '/help', admin: '/help/admin' };

/** Поиск без учёта регистра и «ё». */
function normalize(value: string): string {
  return value.toLowerCase().replaceAll('ё', 'е');
}

function sectionMatches(section: HelpSection, query: string): boolean {
  const haystack = normalize(
    [section.title, section.summary, ...section.blocks.map(blockText)].join(' '),
  );
  return query
    .split(/\s+/)
    .filter(Boolean)
    .every((word) => haystack.includes(word));
}

/** Прокрутка к разделу с поправкой на липкую шапку (scroll-mt у секции). */
function scrollToSection(id: string): void {
  // кадр ожидания: на телефоне оглавление сначала сворачивается и сдвигает текст
  requestAnimationFrame(() => {
    document.getElementById(`help-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

function Toc({
  sections,
  numbers,
  activeId,
  onNavigate,
}: {
  sections: HelpSection[];
  numbers: Map<string, number>;
  activeId: string;
  onNavigate?: () => void;
}): ReactNode {
  return (
    <ol className="m-0 flex list-none flex-col gap-0.5 p-0">
      {sections.map((section) => (
        <li key={section.id}>
          <Link
            to={{ hash: section.id }}
            onClick={() => {
              onNavigate?.();
              // повторный клик по тому же пункту: якорь не меняется — крутим сами
              scrollToSection(section.id);
            }}
            aria-current={activeId === section.id ? 'location' : undefined}
            className={cn(
              'flex min-h-9 items-baseline gap-2 rounded-md px-2.5 py-1.5 text-sm outline-none transition-colors duration-150',
              'hover:bg-accent hover:text-accent-foreground focus-visible:ring-2 focus-visible:ring-ring',
              activeId === section.id ? 'bg-primary-tint font-medium text-primary' : 'text-muted-foreground',
            )}
          >
            <span className="tabular w-5 shrink-0 text-right text-xs">{numbers.get(section.id)}</span>
            <span className="min-w-0">{section.title}</span>
          </Link>
        </li>
      ))}
    </ol>
  );
}

export default function HelpScreen({ guideId }: { guideId: HelpGuide['id'] }): ReactNode {
  const { hasPermission } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [query, setQuery] = useState('');
  const [tocOpen, setTocOpen] = useState(false);

  const guide = HELP_GUIDES[guideId];
  const showAdminGuide = canSeeAdminGuide((code) => hasPermission(code));
  const allSections = useMemo(() => sectionsFor(guide, 'app'), [guide]);
  // номер раздела не меняется при поиске — по нему раздел называют в разговоре
  const numbers = useMemo(
    () => new Map(allSections.map((section, index) => [section.id, index + 1])),
    [allSections],
  );
  const normalizedQuery = normalize(query.trim());
  const sections = useMemo(
    () =>
      normalizedQuery
        ? allSections.filter((section) => sectionMatches(section, normalizedQuery))
        : allSections,
    [allSections, normalizedQuery],
  );
  const activeId = decodeURIComponent(location.hash.replace(/^#/, ''));

  // Переход по якорю (/help#board из другого экрана, ссылки из текста справки)
  useEffect(() => {
    if (activeId) scrollToSection(activeId);
  }, [activeId, guideId]);

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PageHeader
        title="Справка"
        subtitle={`${guide.title} · версия ${guide.version} от ${guide.updated}`}
        extra={
          <>
            {showAdminGuide ? (
              <SegmentedControl<HelpGuide['id']>
                size="sm"
                value={guideId}
                onChange={(value) => navigate(GUIDE_PATHS[value])}
                options={[
                  { value: 'user', label: 'Пользователю' },
                  { value: 'admin', label: 'Администратору' },
                ]}
              />
            ) : null}
            <Button variant="outline" size="sm" className="print:hidden" onClick={() => window.print()}>
              <Printer aria-hidden="true" />
              Печать / PDF
            </Button>
          </>
        }
      />

      <div className="grid min-w-0 gap-4 lg:grid-cols-[240px_minmax(0,1fr)] lg:gap-6">
        <aside className="flex min-w-0 flex-col gap-3 print:hidden lg:sticky lg:top-20 lg:max-h-[calc(100vh-6rem)] lg:self-start lg:overflow-y-auto">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Поиск по справке"
              aria-label="Поиск по справке"
              className="w-full pl-8 pr-8"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="Очистить поиск"
                className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-0.5 text-muted-foreground hover:text-foreground"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            ) : null}
          </div>

          {/* < lg: оглавление свёрнуто, чтобы текст начинался на первом экране телефона */}
          <Collapsible open={tocOpen} onOpenChange={setTocOpen} className="rounded-lg border bg-card lg:hidden">
            <CollapsibleTrigger asChild>
              <button
                type="button"
                className="flex min-h-11 w-full items-center gap-2 px-3 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <ListTree className="size-4 text-muted-foreground" aria-hidden="true" />
                Содержание
                <span className="tabular text-xs font-normal text-muted-foreground">{sections.length}</span>
                <ChevronDown
                  className={cn('ml-auto size-4 text-muted-foreground transition-transform', tocOpen && 'rotate-180')}
                  aria-hidden="true"
                />
              </button>
            </CollapsibleTrigger>
            <CollapsibleContent className="border-t px-1.5 py-1.5">
              <Toc
                sections={sections}
                numbers={numbers}
                activeId={activeId}
                onNavigate={() => setTocOpen(false)}
              />
            </CollapsibleContent>
          </Collapsible>
          <nav aria-label="Содержание справки" className="hidden lg:block">
            <Toc sections={sections} numbers={numbers} activeId={activeId} />
          </nav>
        </aside>

        <div className="flex min-w-0 flex-col gap-4">
          {sections.length === 0 ? (
            <EmptyState
              illustration="search-empty"
              title="Ничего не нашлось"
              description="Попробуйте другое слово: название раздела меню, действие или термин."
              actions={
                <Button variant="outline" onClick={() => setQuery('')}>
                  Сбросить поиск
                </Button>
              }
            />
          ) : (
            sections.map((section) => (
              <section
                key={section.id}
                id={`help-${section.id}`}
                aria-labelledby={`help-${section.id}-title`}
                className="scroll-mt-20 rounded-lg border bg-card p-4 shadow-card sm:p-6 print:break-inside-auto print:border-0 print:p-0 print:shadow-none"
              >
                <h2 id={`help-${section.id}-title`} className="m-0 mb-3 flex items-baseline gap-2 text-lg font-semibold text-balance">
                  <span className="tabular text-sm text-primary">{numbers.get(section.id)}</span>
                  {section.title}
                </h2>
                <HelpBlocks blocks={section.blocks} />
              </section>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
