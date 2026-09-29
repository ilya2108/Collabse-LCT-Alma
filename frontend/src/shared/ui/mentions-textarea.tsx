import { AtSign } from 'lucide-react';
import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode, type TextareaHTMLAttributes } from 'react';
import { Textarea } from '@/shared/ui/textarea';
import { cn } from '@/shared/lib/cn';

/**
 * MentionsTextarea (redesign.md §3.10): textarea комментариев — «@» открывает
 * поповер выбора коллеги и подставляет имя в текст. Контракт заморожен (§9):
 * { value; onChange; users: {id, name}[] }. Список — свой листбокс в стиле
 * cmdk-поповера (стрелки ↑/↓, Enter/Tab — выбрать, Esc — закрыть).
 */

export interface MentionUser {
  id: string;
  name: string;
}

export interface MentionsTextareaProps
  extends Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, 'value' | 'onChange'> {
  value: string;
  onChange: (value: string) => void;
  users: MentionUser[];
}

interface MentionToken {
  /** Индекс символа «@». */
  start: number;
  /** Набранный после «@» префикс. */
  query: string;
}

/** Токен упоминания под кареткой: «@» в начале слова, без пробелов внутри. */
function tokenAtCaret(text: string, caret: number): MentionToken | null {
  const before = text.slice(0, caret);
  const at = before.lastIndexOf('@');
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(before[at - 1] ?? '')) return null;
  const query = before.slice(at + 1);
  if (/\s/.test(query)) return null;
  return { start: at, query };
}

export function MentionsTextarea({
  value,
  onChange,
  users,
  className,
  onKeyDown,
  ...rest
}: MentionsTextareaProps): ReactNode {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [token, setToken] = useState<MentionToken | null>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const listId = 'mentions-listbox';

  const matches = useMemo(() => {
    if (!token) return [];
    const q = token.query.toLocaleLowerCase('ru');
    return users.filter((u) => u.name.toLocaleLowerCase('ru').includes(q)).slice(0, 8);
  }, [token, users]);

  const open = token !== null && matches.length > 0;

  const syncToken = (next: string, caret: number): void => {
    setToken(tokenAtCaret(next, caret));
    setActiveIndex(0);
  };

  const pick = (user: MentionUser): void => {
    if (!token) return;
    const caret = textareaRef.current?.selectionStart ?? value.length;
    const inserted = `@${user.name} `;
    const next = value.slice(0, token.start) + inserted + value.slice(caret);
    onChange(next);
    setToken(null);
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (!el) return;
      const pos = token.start + inserted.length;
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (open) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % matches.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActiveIndex((i) => (i - 1 + matches.length) % matches.length);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault();
        const user = matches[activeIndex];
        if (user) pick(user);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setToken(null);
        return;
      }
    }
    onKeyDown?.(e);
  };

  return (
    <div className="relative w-full">
      <Textarea
        {...rest}
        ref={textareaRef}
        value={value}
        role="combobox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-autocomplete="list"
        className={cn('min-h-16', className)}
        onChange={(e) => {
          onChange(e.target.value);
          syncToken(e.target.value, e.target.selectionStart ?? e.target.value.length);
        }}
        onKeyDown={handleKeyDown}
        onClick={(e) => {
          const el = e.currentTarget;
          syncToken(el.value, el.selectionStart ?? el.value.length);
        }}
        onBlur={() => {
          // даём click по пункту списка успеть отработать
          setTimeout(() => setToken(null), 150);
        }}
      />
      {open ? (
        <ul
          id={listId}
          role="listbox"
          aria-label="Упомянуть коллегу"
          className="absolute bottom-full left-0 z-50 mb-1 max-h-56 w-64 overflow-y-auto rounded-md border bg-popover p-1 shadow-overlay"
        >
          {matches.map((user, index) => (
            <li
              key={user.id}
              role="option"
              aria-selected={index === activeIndex}
              className={cn(
                'flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm',
                index === activeIndex ? 'bg-accent text-accent-foreground' : 'text-foreground',
              )}
              onMouseEnter={() => setActiveIndex(index)}
              onMouseDown={(e) => {
                e.preventDefault();
                pick(user);
              }}
            >
              <AtSign className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 truncate">{user.name}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
