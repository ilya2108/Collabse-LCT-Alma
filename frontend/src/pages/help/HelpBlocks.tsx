import { Info, TriangleAlert } from 'lucide-react';
import { Fragment, useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/shared/lib/cn';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { parseInline, type HelpBlock, type Inline } from './content/types';

/**
 * Рендер блоков справки (модель — content/types.ts). Всё в токенах темы, без
 * фиксированных ширин: на 375 px таблицы прокручиваются внутри своего блока,
 * снимки с телефона встают в ряд с переносом.
 */

/** Снимки экрана лежат в public/help/img (копии docs/manual/img, уменьшенные для веба). */
export const HELP_IMG_BASE = '/help/img/';

function InlineLink({ href, children }: { href: string; children: ReactNode }): ReactNode {
  const className = 'font-medium text-primary underline-offset-4 hover:underline';
  if (href.startsWith('#')) {
    return (
      <Link to={{ hash: href }} className={className}>
        {children}
      </Link>
    );
  }
  // /api/* отдаёт backend (Swagger), а не SPA — обычная ссылка в новой вкладке
  if (href.startsWith('/') && !href.startsWith('/api/')) {
    return (
      <Link to={href} className={className}>
        {children}
      </Link>
    );
  }
  return (
    <a href={href} target="_blank" rel="noreferrer" className={className}>
      {children}
    </a>
  );
}

export function InlineText({ text }: { text: Inline }): ReactNode {
  return parseInline(text).map((token, index) => {
    switch (token.t) {
      case 'b':
        return (
          <strong key={index} className="font-semibold text-foreground">
            {token.text}
          </strong>
        );
      case 'code':
        return (
          <code
            key={index}
            className="rounded-sm bg-muted px-1 py-0.5 font-mono text-[0.85em] break-words text-foreground"
          >
            {token.text}
          </code>
        );
      case 'link':
        return (
          <InlineLink key={index} href={token.href}>
            {token.text}
          </InlineLink>
        );
      default:
        return <Fragment key={index}>{token.text}</Fragment>;
    }
  });
}

type FigureBlock = Extract<HelpBlock, { kind: 'figure' }>;

function Figure({ block }: { block: FigureBlock }): ReactNode {
  // снимка нет (ещё не перенесён в public/help) — блок не показываем, текст справки важнее
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  const src = `${HELP_IMG_BASE}${block.img}`;
  return (
    <figure
      className={cn(
        'm-0 flex flex-col gap-1.5 break-inside-avoid',
        block.mobile ? 'w-full max-w-[220px]' : 'w-full',
      )}
    >
      <a href={src} target="_blank" rel="noreferrer" aria-label={`${block.alt} — открыть снимок`}>
        <img
          src={src}
          alt={block.alt}
          loading="lazy"
          decoding="async"
          onError={() => setBroken(true)}
          className="block h-auto w-full rounded-md border border-border bg-muted"
        />
      </a>
      <figcaption className="text-center text-xs text-muted-foreground">{block.caption}</figcaption>
    </figure>
  );
}

function Block({ block }: { block: HelpBlock }): ReactNode {
  switch (block.kind) {
    case 'p':
      return (
        <p className={cn('m-0 text-pretty', block.lead ? 'text-[15px] text-foreground' : 'text-sm')}>
          <InlineText text={block.text} />
        </p>
      );
    case 'h3':
      return <h3 className="m-0 text-sm font-semibold">{block.text}</h3>;
    case 'steps':
      return (
        <ol className="m-0 list-decimal space-y-1.5 pl-5 text-sm marker:font-semibold marker:text-primary">
          {block.items.map((item, index) => (
            <li key={index} className="pl-1 text-pretty">
              <InlineText text={item} />
            </li>
          ))}
        </ol>
      );
    case 'list':
      return (
        <ul className="m-0 list-disc space-y-1.5 pl-5 text-sm marker:text-primary">
          {block.items.map((item, index) => (
            <li key={index} className="pl-1 text-pretty">
              <InlineText text={item} />
            </li>
          ))}
        </ul>
      );
    case 'table':
      return (
        <div className="rounded-md border border-border">
          <Table>
            <TableHeader>
              <TableRow className="hover:bg-transparent">
                {block.head.map((cell) => (
                  <TableHead key={cell}>{cell}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {block.rows.map((row, rowIndex) => (
                <TableRow key={rowIndex} className="hover:bg-transparent">
                  {row.map((cell, cellIndex) => (
                    <TableCell
                      key={cellIndex}
                      className={cn(
                        'align-top text-pretty',
                        // первая колонка — термин: не переносим посреди кода/названия
                        cellIndex === 0 ? 'min-w-28 font-medium' : 'min-w-44',
                      )}
                    >
                      <InlineText text={cell} />
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      );
    case 'callout': {
      const warn = block.tone === 'warn';
      const Icon = warn ? TriangleAlert : Info;
      return (
        <div
          role="note"
          className={cn(
            'flex gap-3 rounded-md border-l-4 px-4 py-3 text-sm break-inside-avoid',
            warn
              ? 'border-status-warning bg-status-warning-tint'
              : 'border-primary bg-primary-tint',
          )}
        >
          <Icon
            className={cn('mt-0.5 size-4 shrink-0', warn ? 'text-status-warning-deep' : 'text-primary')}
            aria-hidden="true"
          />
          <p className="m-0 min-w-0 text-pretty">
            {block.title ? <strong className="font-semibold">{block.title}. </strong> : null}
            <InlineText text={block.text} />
          </p>
        </div>
      );
    }
    case 'flow':
      return (
        <ol className="m-0 grid list-none gap-2 p-0 sm:grid-cols-2 xl:grid-cols-4">
          {block.steps.map((step, index) => (
            <li
              key={step.title}
              className="rounded-md border border-border border-t-2 border-t-primary bg-card px-3 py-2.5 text-sm break-inside-avoid"
            >
              <span className="mb-0.5 block font-semibold text-foreground">
                <span className="tabular mr-1.5 text-primary">{index + 1}</span>
                {step.title}
              </span>
              <span className="text-pretty text-muted-foreground">
                <InlineText text={step.text} />
              </span>
            </li>
          ))}
        </ol>
      );
    case 'code':
      return (
        <pre className="m-0 overflow-x-auto rounded-md bg-sidebar px-4 py-3 font-mono text-xs leading-relaxed text-white">
          {block.text}
        </pre>
      );
    case 'figure':
      return <Figure block={block} />;
    case 'faq':
      return (
        <div className="flex flex-col gap-1 break-inside-avoid">
          <h3 className="m-0 text-sm font-semibold">{block.q}</h3>
          {block.a.map((answer, index) => (
            <p key={index} className="m-0 text-sm text-pretty text-muted-foreground">
              <InlineText text={answer} />
            </p>
          ))}
        </div>
      );
  }
}

/**
 * Блоки раздела по порядку. Подряд идущие снимки с телефона — одним рядом
 * с переносом: на 375 px один под другим, на десктопе — рядом.
 */
export function HelpBlocks({ blocks }: { blocks: HelpBlock[] }): ReactNode {
  const groups: Array<HelpBlock | FigureBlock[]> = [];
  for (const block of blocks) {
    const previous = groups[groups.length - 1];
    if (block.kind === 'figure' && block.mobile) {
      if (Array.isArray(previous)) previous.push(block);
      else groups.push([block]);
    } else {
      groups.push(block);
    }
  }
  return (
    <div className="flex flex-col gap-3">
      {groups.map((group, index) =>
        Array.isArray(group) ? (
          <div key={index} className="flex flex-wrap justify-center gap-4">
            {group.map((figure) => (
              <Figure key={figure.img} block={figure} />
            ))}
          </div>
        ) : (
          <Block key={index} block={group} />
        ),
      )}
    </div>
  );
}
