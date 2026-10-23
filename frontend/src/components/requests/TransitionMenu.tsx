import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertTriangle, ChevronDown, Loader2, Undo2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import {
  listRequestTransitions,
  transitionWithFiles,
} from '@/shared/api/endpoints/requests';
import { transitionExpectations } from '@/shared/api/transitionContext';
import type { AvailableTransition, RequestItem } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { TransitionCommentModal } from './TransitionCommentModal';

/**
 * «Перевести на этап ▾» (ux.md §8.1, redesign.md §6.4): DropdownMenu строится
 * строго из `GET /requests/{id}/transitions`; возвраты — danger-подсекция
 * «↩ Возврат на…». `condition` — только подсказка: недоступные пункты
 * помечаются AlertTriangle + строкой причины, но не блокируются.
 * Любой переход идёт через диалог: необязательный комментарий и файлы (ФТ2, ФТ3),
 * обязательный — у возвратов и переходов с `requires_comment`.
 */

interface TransitionMenuProps {
  request: RequestItem;
  onTransitioned?: () => void;
  /** Классы кнопки-триггера (во всю ширину на телефоне — в шапке карточки). */
  triggerClassName?: string;
}

export function TransitionMenu({
  request,
  onTransitioned,
  triggerClassName,
}: TransitionMenuProps): ReactNode {
  const queryClient = useQueryClient();
  const { hasPermission } = useAuth();
  const [pending, setPending] = useState<AvailableTransition | null>(null);
  const canAttach = hasPermission('requests:files', request.workflow_type);

  const transitionsQuery = useQuery({
    queryKey: ['request-transitions', request.id, request.status.id],
    queryFn: ({ signal }) => listRequestTransitions(request.id, signal),
  });
  const transitions = transitionsQuery.data?.items ?? [];

  const mutation = useMutation({
    mutationFn: ({
      transition,
      comment,
      files,
    }: {
      transition: AvailableTransition;
      comment?: string;
      files?: File[];
    }) =>
      transitionWithFiles(
        request.id,
        {
          to_status_id: transition.to_status_id,
          comment: comment || undefined,
          version: request.version,
          ...transitionExpectations(request),
        },
        files,
      ),
    onSuccess: (updated, { files }) => {
      const attached = files?.length ? ` · файлов: ${files.length}` : '';
      toastSuccess(`Заявка переведена на этап «${updated.status.name}»${attached}`);
      void queryClient.invalidateQueries({ queryKey: ['request', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['request-history', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['request-transitions', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['request-files', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['board-requests'] });
      setPending(null);
      onTransitioned?.();
    },
    onError: () => {
      // тост показал глобальный обработчик; данные могли устареть — перечитываем.
      // Диалог остаётся открытым: комментарий и файлы не теряются, можно повторить.
      void queryClient.invalidateQueries({ queryKey: ['request', request.id] });
      void queryClient.invalidateQueries({ queryKey: ['request-transitions', request.id] });
    },
  });

  // ФТ2: комментарий можно оставить на любом переходе — диалог открывается всегда
  const run = (transition: AvailableTransition): void => setPending(transition);

  const renderItem = (transition: AvailableTransition, isReturn: boolean): ReactNode => {
    const label = transition.to_status
      ? `${transition.name} → «${transition.to_status.name}»`
      : transition.name;
    return (
      <DropdownMenuItem
        key={transition.transition_id}
        variant={isReturn ? 'destructive' : 'default'}
        onSelect={() => run(transition)}
      >
        {isReturn ? <Undo2 aria-hidden="true" /> : null}
        <span className="min-w-0 flex-1">
          <span className="block truncate">{label}</span>
          {!transition.available && transition.unavailable_reason ? (
            <span className="mt-0.5 flex items-center gap-1 text-xs text-status-warning-deep">
              <AlertTriangle className="size-3! shrink-0" aria-hidden="true" />
              {transition.unavailable_reason}
            </span>
          ) : null}
        </span>
      </DropdownMenuItem>
    );
  };

  const forward = transitions.filter((t) => t.kind === 'forward');
  const returns = transitions.filter((t) => t.kind === 'return');

  if (!transitionsQuery.isLoading && transitions.length === 0) {
    return null;
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            className={triggerClassName}
            disabled={transitionsQuery.isLoading || mutation.isPending}
          >
            {mutation.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : null}
            Перевести на этап
            <ChevronDown aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-64">
          {forward.map((t) => renderItem(t, false))}
          {returns.length > 0 ? (
            <>
              {forward.length > 0 ? <DropdownMenuSeparator /> : null}
              <DropdownMenuLabel>↩ Возврат на…</DropdownMenuLabel>
              {returns.map((t) => renderItem(t, true))}
            </>
          ) : null}
        </DropdownMenuContent>
      </DropdownMenu>
      {pending ? (
        <TransitionCommentModal
          open
          actionName={pending.name}
          toStatusName={pending.to_status?.name ?? ''}
          isReturn={pending.kind === 'return'}
          required={pending.requires_comment || pending.kind === 'return'}
          canAttach={canAttach}
          loading={mutation.isPending}
          onConfirm={({ comment, files }) =>
            mutation.mutate({ transition: pending, comment, files })
          }
          onCancel={() => setPending(null)}
        />
      ) : null}
    </>
  );
}
