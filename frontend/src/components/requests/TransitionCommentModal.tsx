import { zodResolver } from '@hookform/resolvers/zod';
import { FileText, Loader2, Paperclip, X } from 'lucide-react';
import { useForm } from 'react-hook-form';
import { z } from 'zod';
import { useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { formatFileSize } from '@/shared/api/endpoints/files';
import {
  REQUEST_FILE_ACCEPT,
  REQUEST_FILE_FORMATS_LABEL,
  REQUEST_FILE_MAX_MB,
  TRANSITION_COMMENT_MAX,
  TRANSITION_FILES_MAX,
  requestFileProblem,
} from '@/shared/api/endpoints/requests';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Form, FormControl, FormField, FormItem, FormMessage } from '@/shared/ui/form';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Диалог перехода по статусу (ux.md §7.2, redesign.md §5.2; ТЗ ФТ2, ФТ3): открывается на
 * ЛЮБОМ переходе — из меню карточки и после drag&drop на доске. Комментарий необязателен,
 * кроме возвратов и переходов с `requires_comment` (`required`): там без него сервер
 * вернёт 400 — не доводим, zod-схема + inline-ошибка под полем. Лимит — 4000 символов,
 * как на сервере. Ctrl/⌘+Enter — перевести. Файлы (при праве `requests:files`) попадут в
 * историю под этим переходом — «файлы в статусах»; формат и размер проверяются до загрузки.
 */

export interface TransitionConfirmValues {
  comment: string;
  files: File[];
}

interface TransitionCommentModalProps {
  open: boolean;
  /** Название перехода («Вернуть в переговоры») и целевой этап. */
  actionName: string;
  toStatusName: string;
  isReturn: boolean;
  /** Комментарий обязателен: возврат или `requires_comment` у перехода. */
  required: boolean;
  /** Можно приложить файлы к переходу (право `requests:files` в процессе заявки). */
  canAttach?: boolean;
  loading: boolean;
  onConfirm: (values: TransitionConfirmValues) => void;
  onCancel: () => void;
}

function buildSchema(required: boolean, isReturn: boolean) {
  const comment = z
    .string()
    .trim()
    .max(TRANSITION_COMMENT_MAX, `Не длиннее ${TRANSITION_COMMENT_MAX} символов`);
  return z.object({
    comment: required
      ? comment.min(
          1,
          isReturn
            ? 'Укажите причину возврата — без неё переход невозможен'
            : 'Этот переход требует комментарий',
        )
      : comment,
  });
}

type FormValues = z.infer<ReturnType<typeof buildSchema>>;

export function TransitionCommentModal({
  open,
  actionName,
  toStatusName,
  isReturn,
  required,
  canAttach = false,
  loading,
  onConfirm,
  onCancel,
}: TransitionCommentModalProps): ReactNode {
  const schema = useMemo(() => buildSchema(required, isReturn), [required, isReturn]);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    mode: 'onTouched',
    defaultValues: { comment: '' },
  });
  const [files, setFiles] = useState<File[]>([]);
  const [fileProblems, setFileProblems] = useState<string[]>([]);
  const fileInputId = useId();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const commentLength = form.watch('comment')?.length ?? 0;

  const submit = form.handleSubmit((values) =>
    onConfirm({ comment: values.comment.trim(), files }),
  );

  const addFiles = (list: FileList | null): void => {
    if (!list) return;
    const problems: string[] = [];
    const next = [...files];
    for (const file of Array.from(list)) {
      const problem = requestFileProblem(file);
      if (problem) {
        problems.push(problem);
      } else if (next.length >= TRANSITION_FILES_MAX) {
        problems.push(`Не больше ${TRANSITION_FILES_MAX} файлов на один переход`);
        break;
      } else if (!next.some((f) => f.name === file.name && f.size === file.size)) {
        next.push(file);
      }
    }
    setFiles(next);
    setFileProblems(problems);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      if (!loading) void submit();
    }
  };

  const title = isReturn
    ? 'Причина возврата'
    : required
      ? 'Комментарий к переходу'
      : 'Перевод на этап';
  const description = required
    ? 'Комментарий обязателен и попадёт в историю заявки.'
    : canAttach
      ? 'Комментарий и файлы необязательны — они попадут в историю заявки под этим переходом.'
      : 'Комментарий необязателен — он попадёт в историю заявки под этим переходом.';

  return (
    <Dialog open={open} onOpenChange={(next) => { if (!next && !loading) onCancel(); }}>
      <DialogContent className="sm:max-w-[480px]">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            {actionName} → этап «{toStatusName}». {description}
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={(event) => void submit(event)} className="grid min-w-0 gap-4">
            <FormField
              control={form.control}
              name="comment"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Textarea
                      {...field}
                      rows={3}
                      autoFocus
                      maxLength={TRANSITION_COMMENT_MAX}
                      onKeyDown={onKeyDown}
                      placeholder={
                        isReturn
                          ? 'Почему возвращаем на предыдущий этап…'
                          : required
                            ? 'Комментарий к переходу…'
                            : 'Комментарий к переходу (необязательно)…'
                      }
                      aria-label={isReturn ? 'Причина возврата' : 'Комментарий к переходу'}
                    />
                  </FormControl>
                  <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                    <span>Ctrl/⌘ + Enter — {isReturn ? 'вернуть' : 'перевести'}</span>
                    <span
                      aria-live="polite"
                      className={commentLength > TRANSITION_COMMENT_MAX * 0.9 ? 'text-status-warning-deep' : undefined}
                    >
                      {commentLength > 0 ? `${commentLength} / ${TRANSITION_COMMENT_MAX}` : null}
                    </span>
                  </div>
                  <FormMessage />
                </FormItem>
              )}
            />

            {canAttach ? (
              <div className="grid min-w-0 gap-2">
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    ref={fileInputRef}
                    id={fileInputId}
                    type="file"
                    multiple
                    accept={REQUEST_FILE_ACCEPT.join(',')}
                    className="sr-only"
                    disabled={loading}
                    onChange={(event) => addFiles(event.target.files)}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    disabled={loading || files.length >= TRANSITION_FILES_MAX}
                    onClick={() => fileInputRef.current?.click()}
                  >
                    <Paperclip aria-hidden="true" />
                    Приложить файл
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    {REQUEST_FILE_FORMATS_LABEL} · до {REQUEST_FILE_MAX_MB} МБ
                  </span>
                </div>
                {files.length > 0 ? (
                  <ul className="m-0 grid list-none gap-1.5 p-0" aria-label="Файлы перехода">
                    {files.map((file) => (
                      <li
                        key={`${file.name}-${file.size}`}
                        className="flex min-w-0 items-center gap-2 rounded-md border bg-muted/40 px-2 py-1.5 text-sm"
                      >
                        <FileText className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                        <span className="min-w-0 flex-1 truncate">{file.name}</span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {formatFileSize(file.size)}
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-7 shrink-0"
                          disabled={loading}
                          aria-label={`Убрать «${file.name}»`}
                          onClick={() => setFiles(files.filter((f) => f !== file))}
                        >
                          <X aria-hidden="true" />
                        </Button>
                      </li>
                    ))}
                  </ul>
                ) : null}
                {fileProblems.length > 0 ? (
                  <ul className="m-0 list-none p-0 text-xs text-status-danger-deep" role="alert">
                    {fileProblems.map((problem) => (
                      <li key={problem}>{problem}</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            <DialogFooter>
              <Button type="button" variant="outline" disabled={loading} onClick={onCancel}>
                Отмена
              </Button>
              <Button
                type="submit"
                variant={isReturn ? 'destructive' : 'default'}
                disabled={loading}
                aria-live="polite"
              >
                {loading ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                {loading
                  ? files.length > 0
                    ? 'Загружаем файлы…'
                    : 'Сохраняем…'
                  : isReturn
                    ? 'Вернуть'
                    : 'Перевести'}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
