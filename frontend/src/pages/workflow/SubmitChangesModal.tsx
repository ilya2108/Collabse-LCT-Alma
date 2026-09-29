import { Info, Loader2, Send, TriangleAlert } from 'lucide-react';
import { useEffect, useId, useState, type ReactNode } from 'react';
import type {
  WorkflowChangeImpact,
  WorkflowOperation,
  WorkflowSchema,
} from '@/shared/api/types';
import { ImpactSummary, OperationsDiff } from '@/components/workflow/OperationsDiff';
import { Alert, AlertTitle } from '@/shared/ui/alert';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Label } from '@/shared/ui/label';
import { Separator } from '@/shared/ui/separator';
import { Textarea } from '@/shared/ui/textarea';

/**
 * Impact-preview отправки пакета операций (redesign.md §6.6): Dialog с диффом
 * плашками added/renamed/deleted + «будет перенесено N заявок» + комментарий
 * для администратора. Безопасный пакет от admin применяется сразу,
 * опасный всегда уходит в pending.
 */

interface SubmitChangesModalProps {
  open: boolean;
  operations: WorkflowOperation[];
  schema: WorkflowSchema;
  impact: WorkflowChangeImpact | null;
  /** impact-preview не ответил — отправка возможна, но без предпросмотра. */
  impactUnavailable: boolean;
  dangerous: boolean;
  isAdmin: boolean;
  submitting: boolean;
  onSubmit: (comment: string) => void;
  onCancel: () => void;
}

export function SubmitChangesModal({
  open,
  operations,
  schema,
  impact,
  impactUnavailable,
  dangerous,
  isAdmin,
  submitting,
  onSubmit,
  onCancel,
}: SubmitChangesModalProps): ReactNode {
  const commentId = useId();
  const [comment, setComment] = useState('');
  useEffect(() => {
    if (open) setComment('');
  }, [open]);

  const appliesImmediately = isAdmin && !dangerous;
  const okText = appliesImmediately ? 'Опубликовать изменения' : 'Отправить на согласование';

  return (
    <Dialog open={open} onOpenChange={(next) => (!next && !submitting ? onCancel() : undefined)}>
      <DialogContent className="max-h-[85vh] gap-4 overflow-y-auto sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle className="text-balance">Изменения схемы «{schema.name}»</DialogTitle>
        </DialogHeader>

        {appliesImmediately ? (
          <Alert variant="info">
            <Info aria-hidden="true" />
            <AlertTitle>Пакет без опасных операций: будет применён сразу для всех</AlertTitle>
          </Alert>
        ) : (
          <Alert variant={dangerous ? 'warning' : 'info'}>
            {dangerous ? <TriangleAlert aria-hidden="true" /> : <Info aria-hidden="true" />}
            <AlertTitle className="text-balance">
              {dangerous
                ? 'Пакет содержит переименование или удаление этапа — потребуется согласование администратора'
                : 'Пакет уйдёт на согласование администратору'}
            </AlertTitle>
          </Alert>
        )}

        <OperationsDiff operations={operations} schema={schema} impact={impact} />
        <Separator />
        {impactUnavailable ? (
          <p className="text-sm text-status-warning-deep">
            Предпросмотр влияния недоступен — число переносимых заявок будет показано
            администратору при согласовании.
          </p>
        ) : (
          <ImpactSummary impact={impact} />
        )}

        <div className="space-y-1.5">
          <Label htmlFor={commentId}>Комментарий для администратора</Label>
          <Textarea
            id={commentId}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            rows={2}
            maxLength={500}
            placeholder="Зачем меняем схему (видно при согласовании)…"
          />
        </div>

        <DialogFooter>
          <Button variant="outline" disabled={submitting} onClick={onCancel}>
            Отмена
          </Button>
          <Button
            variant={dangerous ? 'destructive' : 'default'}
            disabled={submitting}
            onClick={() => onSubmit(comment.trim())}
          >
            {submitting ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <Send aria-hidden="true" />
            )}
            {okText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
