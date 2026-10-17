import ReactECharts from 'echarts-for-react';
import type { EChartsOption } from 'echarts';
import type ReactEChartsCore from 'echarts-for-react';
import { ImageDown } from 'lucide-react';
import { Component, useCallback, useRef, type ReactNode } from 'react';
import { ErrorBrokenIllustration, SearchEmptyIllustration } from '@/app/localIllustrations';
import { CRM_THEME, registerCrmTheme } from '@/shared/config/echartsTheme';
import { toastError } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';
import { downloadChartPng } from './exportImage';

// Тема 'crm' (redesign.md §2.6): регистрируется один раз при загрузке чанка дашборда.
registerCrmTheme();

/**
 * Карточка виджета дашборда (redesign.md §6.3): заголовок 16/24 и подзаголовок —
 * что именно и за какой период показано; кнопка-камера `ImageDown` («Скачать PNG»:
 * график с шапкой — название, подзаголовок, фильтры, дата формирования; без шапки
 * картинку не вставить в отчёт), компактные пустое и ошибочное состояния с
 * иллюстрациями (§3.5) и собственный error-boundary — ошибка одного графика не роняет
 * дашборд (ux.md §6.3).
 */

export interface ChartClickParams {
  /** name сегмента/категории. */
  name: string;
  /** Ключ данных, положенный виджетом в data[i].key. */
  dataKey?: string;
  seriesName?: string;
}

interface ChartCardProps {
  title: string;
  /** Что и за какой период: «Созданы за 01.09 — 30.09 · на текущем этапе». */
  subtitle?: string;
  /** Строки контекста для шапки PNG: процесс, фильтры. */
  exportCaption?: string[];
  option: EChartsOption | null;
  loading?: boolean;
  height?: number;
  empty?: boolean;
  emptyText?: string;
  emptyAction?: ReactNode;
  onSegmentClick?: (params: ChartClickParams) => void;
  /** Регистрация инстанса для композитного PNG всего дашборда. */
  onChartReady?: (id: string, getPng: () => string | null) => void;
  chartId: string;
  /** Доп. контролы в шапке карточки (например, тумблер режима). */
  extra?: ReactNode;
}

interface BoundaryState {
  failed: boolean;
}

class ChartErrorBoundary extends Component<{ children: ReactNode }, BoundaryState> {
  state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  override render(): ReactNode {
    if (this.state.failed) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
          <ErrorBrokenIllustration height={96} />
          <p className="text-sm text-muted-foreground">График не отрисовался</p>
          <Button variant="outline" size="sm" onClick={() => this.setState({ failed: false })}>
            Повторить
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function ChartCard({
  title,
  subtitle,
  exportCaption,
  option,
  loading = false,
  height = 320,
  empty = false,
  emptyText = 'Нет данных за выбранный период',
  emptyAction,
  onSegmentClick,
  onChartReady,
  chartId,
  extra,
}: ChartCardProps): ReactNode {
  const chartRef = useRef<ReactEChartsCore | null>(null);

  const exportPng = useCallback((): string | null => {
    const instance = chartRef.current?.getEchartsInstance();
    if (!instance) return null;
    return instance.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: '#FFFFFF' });
  }, []);

  const downloadPng = (): void => {
    const dataUrl = exportPng();
    if (!dataUrl) return;
    downloadChartPng({ title, subtitle, caption: exportCaption, dataUrl }, `${chartId}-${title}`).catch(
      (error: unknown) => toastError(error, { title: 'Не удалось собрать PNG' }),
    );
  };

  const attachRef = (node: ReactEChartsCore | null): void => {
    chartRef.current = node;
    if (node && onChartReady) onChartReady(chartId, exportPng);
  };

  let body: ReactNode;
  if (loading) {
    body = <div className="shimmer w-full rounded-md" style={{ height }} aria-hidden="true" />;
  } else if (empty || !option) {
    body = (
      <div
        className="flex flex-col items-center justify-center gap-2 text-center"
        style={{ minHeight: height }}
      >
        <SearchEmptyIllustration height={96} />
        <p className="text-sm text-muted-foreground">{emptyText}</p>
        {emptyAction}
      </div>
    );
  } else {
    body = (
      <ChartErrorBoundary>
        <ReactECharts
          ref={attachRef}
          option={option}
          theme={CRM_THEME}
          notMerge
          style={{ height, width: '100%' }}
          opts={{ renderer: 'canvas' }}
          onEvents={
            onSegmentClick
              ? {
                  click: (params: {
                    name?: string;
                    seriesName?: string;
                    data?: { key?: string } | number | null;
                  }) => {
                    const dataKey =
                      params.data && typeof params.data === 'object' && 'key' in params.data
                        ? params.data.key
                        : undefined;
                    onSegmentClick({
                      name: params.name ?? '',
                      dataKey,
                      seriesName: params.seriesName,
                    });
                  },
                }
              : undefined
          }
        />
      </ChartErrorBoundary>
    );
  }

  return (
    <section className="flex h-full flex-col rounded-lg border border-border bg-card shadow-card">
      <header className="flex flex-wrap items-start justify-between gap-2 px-5 pb-1 pt-4">
        <div className="min-w-0 flex-1 basis-48">
          {/* title-атрибут: полное название при усечении длинного заголовка (§1.3) */}
          <h2 className="truncate text-base font-semibold" title={title}>
            {title}
          </h2>
          {subtitle ? (
            <p className="mt-0.5 text-pretty text-xs text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
        <div className="flex min-w-0 max-w-full items-center gap-1.5">
          {/* на узком экране переключатель режимов прокручивается, а не вылезает за карточку */}
          {extra ? <div className="min-w-0 overflow-x-auto">{extra}</div> : null}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-muted-foreground hover:text-foreground"
                onClick={downloadPng}
                disabled={loading || empty || !option}
                aria-label={`Скачать PNG: ${title}`}
              >
                <ImageDown aria-hidden="true" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Скачать PNG</TooltipContent>
          </Tooltip>
        </div>
      </header>
      <div className="min-w-0 flex-1 px-3 pb-3">{body}</div>
    </section>
  );
}
