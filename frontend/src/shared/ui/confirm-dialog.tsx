import { Loader2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { cn } from '@/shared/lib/cn';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog';
import { Button } from '@/shared/ui/button';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';

/**
 * ConfirmDialog (redesign.md §3.8) — красные сценарии с посимвольным вводом.
 * Radix AlertDialog: focus-trap, Esc, scroll-lock, role="alertdialog" из коробки.
 * Кнопка подтверждения активна только при точном совпадении ввода с confirmWord;
 * введённое значение уходит наружу как confirm_name (onConfirm(typed)).
 * Контракт заморожен — менять только правкой redesign.md.
 */
export interface ConfirmDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  tone?: 'danger' | 'default';
  title: string;
  /** Блок фактов плашками, не абзацем. */
  facts?: Array<{ icon?: LucideIcon; label: ReactNode }>;
  /** Например, radio выбора этапа миграции. */
  body?: ReactNode;
  /** Посимвольное подтверждение: кнопка активна только при точном совпадении. */
  confirmWord?: string;
  confirmLabel: string;
  loading?: boolean;
  onConfirm: (typed?: string) => void;
}

/** Эталонное слово с подсветкой уже верно набранных символов. */
function ConfirmWordProgress({ word, typed }: { word: string; typed: string }): ReactNode {
  let matched = 0;
  while (matched < typed.length && matched < word.length && typed[matched] === word[matched]) {
    matched += 1;
  }
  return (
    <span aria-hidden="true" className="select-none break-all font-medium">
      {word.split('').map((char, index) => (
        <span
          key={index}
          className={index < matched ? 'text-status-danger-deep' : 'text-muted-foreground/60'}
        >
          {char}
        </span>
      ))}
    </span>
  );
}

export function ConfirmDialog({
  open,
  onOpenChange,
  tone = 'danger',
  title,
  facts,
  body,
  confirmWord,
  confirmLabel,
  loading,
  onConfirm,
}: ConfirmDialogProps): ReactNode {
  const [typed, setTyped] = useState('');
  useEffect(() => {
    if (open) setTyped('');
  }, [open]);

  const needsWord = Boolean(confirmWord);
  const wordOk = !needsWord || typed === confirmWord;
  const mismatch = needsWord && typed.length > 0 && !confirmWord?.startsWith(typed);

  return (
    <AlertDialog open={open} onOpenChange={(next) => (!loading ? onOpenChange(next) : undefined)}>
      <AlertDialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-balance">{title}</AlertDialogTitle>
          {facts && facts.length > 0 ? (
            <AlertDialogDescription asChild>
              <div className="flex flex-wrap gap-2 pt-1">
                {facts.map((fact, index) => {
                  const Icon = fact.icon;
                  return (
                    <span
                      key={index}
                      className={cn(
                        'inline-flex items-center gap-1.5 rounded-sm px-2.5 py-1 text-xs font-medium',
                        tone === 'danger'
                          ? 'bg-status-danger-tint text-status-danger-deep'
                          : 'bg-muted text-foreground',
                      )}
                    >
                      {Icon ? <Icon className="size-3.5 shrink-0" aria-hidden="true" /> : null}
                      {fact.label}
                    </span>
                  );
                })}
              </div>
            </AlertDialogDescription>
          ) : null}
        </AlertDialogHeader>

        {body}

        {needsWord ? (
          <div className="space-y-1.5">
            <Label htmlFor="confirm-dialog-word" className="text-xs text-muted-foreground">
              Введите название этапа для подтверждения
            </Label>
            <Input
              id="confirm-dialog-word"
              value={typed}
              onChange={(event) => setTyped(event.target.value)}
              autoComplete="off"
              spellCheck={false}
              placeholder={confirmWord}
              aria-invalid={mismatch || undefined}
              className={mismatch ? 'border-destructive' : undefined}
            />
            <div className="text-xs">
              <ConfirmWordProgress word={confirmWord ?? ''} typed={typed} />
            </div>
            {mismatch ? (
              <p className="text-xs text-status-danger-deep" role="alert">
                Название не совпадает — введите точно: «{confirmWord}»
              </p>
            ) : null}
          </div>
        ) : null}

        <AlertDialogFooter>
          <AlertDialogCancel disabled={loading}>Отмена</AlertDialogCancel>
          <Button
            variant={tone === 'danger' ? 'destructive' : 'default'}
            disabled={!wordOk || loading}
            onClick={() => onConfirm(needsWord ? typed : undefined)}
          >
            {loading ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
            {confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
