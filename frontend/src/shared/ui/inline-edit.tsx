import { Pencil } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Input } from '@/shared/ui/input';
import { motionTokens } from '@/shared/lib/motion';
import { cn } from '@/shared/lib/cn';

/**
 * InlineEdit (redesign.md §3.10): атрибуты карточки заявки — клик → input →
 * blur/Enter сохраняет (optimistic), успешное сохранение — вспышка фона
 * success-tint 400ms; Esc отменяет. Контракт заморожен (§9).
 */

export type InlineEditValue = string | number | null;

export interface InlineEditProps {
  value: InlineEditValue;
  onSave: (value: InlineEditValue) => void | Promise<unknown>;
  type: 'text' | 'number' | 'date';
  /** Отображение в режиме просмотра (formatMoney и т.п.). */
  formatter?: (value: InlineEditValue) => ReactNode;
  /** aria-label кнопки редактирования: «Изменить сумму». */
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

function toInputValue(value: InlineEditValue): string {
  if (value === null || value === undefined) return '';
  return String(value);
}

export function InlineEdit({
  value,
  onSave,
  type,
  formatter,
  label,
  placeholder = 'Не задано…',
  disabled = false,
  className,
}: InlineEditProps): ReactNode {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [flash, setFlash] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  /** Enter вызывает blur — не сохраняем второй раз. */
  const committedRef = useRef(false);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  const startEdit = (): void => {
    setDraft(toInputValue(value));
    committedRef.current = false;
    setEditing(true);
  };

  const commit = (): void => {
    if (committedRef.current) return;
    committedRef.current = true;
    setEditing(false);
    const trimmed = draft.trim();
    const next: InlineEditValue =
      trimmed === '' ? null : type === 'number' ? Number(trimmed) : trimmed;
    if (type === 'number' && next !== null && !Number.isFinite(next)) return;
    if (next === value || (next === null && (value === null || value === ''))) return;
    // optimistic: вспышку показываем сразу, откат данных — забота вызывающего
    void onSave(next);
    setFlash((n) => n + 1);
  };

  const cancel = (): void => {
    committedRef.current = true;
    setEditing(false);
  };

  if (editing) {
    return (
      <Input
        ref={inputRef}
        type={type}
        inputMode={type === 'number' ? 'decimal' : undefined}
        value={draft}
        aria-label={label}
        className={cn('h-8', className)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit();
          if (e.key === 'Escape') cancel();
        }}
      />
    );
  }

  const shown = formatter ? formatter(value) : toInputValue(value);
  const empty = value === null || value === undefined || value === '';

  return (
    <button
      type="button"
      disabled={disabled}
      aria-label={label ?? 'Изменить значение'}
      onClick={startEdit}
      className={cn(
        'group relative inline-flex min-h-8 max-w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-sm',
        'transition-[background-color] duration-150',
        disabled ? 'cursor-default' : 'hover:bg-primary-tint',
        className,
      )}
    >
      {/* вспышка success-tint 400ms после сохранения (§2.3 микро-фидбек) */}
      <AnimatePresence>
        {flash > 0 ? (
          <motion.span
            key={flash}
            aria-hidden="true"
            className="absolute inset-0 rounded-md bg-status-success-tint"
            initial={{ opacity: 1 }}
            animate={{ opacity: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: motionTokens.duration.normal + 0.05 }}
          />
        ) : null}
      </AnimatePresence>
      <span className={cn('relative min-w-0 truncate', empty && 'text-muted-foreground')}>
        {empty ? placeholder : shown}
      </span>
      {!disabled ? (
        <Pencil
          aria-hidden="true"
          className="relative size-3.5 shrink-0 text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
        />
      ) : null}
    </button>
  );
}
