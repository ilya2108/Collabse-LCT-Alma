import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { SendHorizontal, Trash2 } from 'lucide-react';
import { AnimatePresence, motion } from 'motion/react';
import { useMemo, useState, type ReactNode } from 'react';
import {
  createRequestComment,
  deleteRequestComment,
  listRequestComments,
} from '@/shared/api/endpoints/requests';
import { listUsers } from '@/shared/api/endpoints/users';
import type { RequestComment } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { formatRelative } from '@/shared/lib/format';
import { springs } from '@/shared/lib/motion';
import { eventRequestId, useSseInvalidate } from '@/shared/lib/useSseInvalidate';
import { cn } from '@/shared/lib/cn';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/shared/ui/alert-dialog';
import { Button } from '@/shared/ui/button';
import { MentionsTextarea } from '@/shared/ui/mentions-textarea';
import { UserAvatar } from '@/shared/ui/UserAvatar';
import { ErrorPanel, LoadingPanel } from './StatePanels';

/**
 * Комментарии заявки — мессенджер (ux.md §8.1, redesign.md §6.4): пузыри
 * bg-muted (свои — bg-primary-tint), @упоминания через MentionsTextarea
 * (§3.10), optimistic-отправка серым, realtime по SSE `request.comment_added`
 * (фолбэк — поллинг 15 с). Удаление — автор или admin, мягкое.
 */

interface PendingComment {
  key: string;
  text: string;
  failed: boolean;
}

export function RequestComments({ requestId }: { requestId: string }): ReactNode {
  const queryClient = useQueryClient();
  const { user, roles, hasRole, hasPermission } = useAuth();
  const [draft, setDraft] = useState('');
  const [pendingComments, setPendingComments] = useState<PendingComment[]>([]);
  const canWrite = hasPermission('comments:write');

  const commentsQuery = useQuery({
    queryKey: ['request-comments', requestId],
    queryFn: ({ signal }) => listRequestComments(requestId, signal),
    // фолбэк realtime — поллинг 15 с (ux.md §8.1)
    refetchInterval: 15_000,
  });

  useSseInvalidate(
    'request.comment_added',
    [['request-comments', requestId]],
    (event) => eventRequestId(event) === requestId || eventRequestId(event) === null,
  );

  // Пользователи для автоподсказки @упоминаний — только admin/head_kam (§10.2)
  const canListUsers = hasRole('admin', 'head_kam');
  const usersQuery = useQuery({
    queryKey: ['users-select', 'all'],
    queryFn: ({ signal }) => listUsers({ signal }),
    enabled: canListUsers,
    staleTime: 60_000,
  });
  // Контракт MentionsTextarea (§3.10): {id, name}; в текст уходит @username
  const mentionUsers = useMemo(
    () => (usersQuery.data?.items ?? []).map((u) => ({ id: u.id, name: u.username })),
    [usersQuery.data],
  );

  const sendMutation = useMutation({
    mutationFn: ({ text }: { key: string; text: string }) => createRequestComment(requestId, text),
    meta: { silent: true },
    onSuccess: (_data, variables) => {
      setPendingComments((prev) => prev.filter((c) => c.key !== variables.key));
      void queryClient.invalidateQueries({ queryKey: ['request-comments', requestId] });
    },
    onError: (_error, variables) => {
      setPendingComments((prev) =>
        prev.map((c) => (c.key === variables.key ? { ...c, failed: true } : c)),
      );
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (commentId: string) => deleteRequestComment(requestId, commentId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['request-comments', requestId] });
    },
  });

  const send = (): void => {
    const text = draft.trim();
    if (!text) return;
    const key = crypto.randomUUID();
    setPendingComments((prev) => [...prev, { key, text, failed: false }]);
    setDraft('');
    sendMutation.mutate({ key, text });
  };

  const retry = (pending: PendingComment): void => {
    setPendingComments((prev) =>
      prev.map((c) => (c.key === pending.key ? { ...c, failed: false } : c)),
    );
    sendMutation.mutate({ key: pending.key, text: pending.text });
  };

  if (commentsQuery.isLoading) return <LoadingPanel rows={4} />;
  if (commentsQuery.isError) {
    return (
      <ErrorPanel
        error={commentsQuery.error}
        title="Не удалось загрузить обсуждение"
        onRetry={() => void commentsQuery.refetch()}
      />
    );
  }

  const comments = commentsQuery.data?.items ?? [];
  const isDeleted = (comment: RequestComment): boolean =>
    Boolean(comment.deleted_at) || comment.text === '(удалено)';

  return (
    <div className="flex min-h-80 flex-col">
      <div className="flex-1">
        {comments.length === 0 && pendingComments.length === 0 ? (
          <p className="text-sm text-muted-foreground">Обсуждения пока нет — напишите первым.</p>
        ) : null}
        <div className="space-y-3">
          <AnimatePresence mode="popLayout" initial={false}>
            {comments.map((comment) => {
              const own = comment.author.id === user?.id;
              const deleted = isDeleted(comment);
              return (
                <motion.div
                  key={comment.id}
                  layout="position"
                  initial={{ opacity: 0, y: 8 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0 }}
                  transition={springs.gentle}
                  className="flex items-start gap-2"
                >
                  <UserAvatar fullName={comment.author.full_name} size={28} tooltip={false} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{comment.author.full_name}</span>
                      <span className="text-xs text-muted-foreground">
                        {formatRelative(comment.created_at)}
                      </span>
                      {!deleted && (roles.includes('admin') || own) ? (
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="size-6 text-muted-foreground hover:text-status-danger-deep"
                              aria-label="Удалить комментарий"
                            >
                              <Trash2 className="size-3.5" aria-hidden="true" />
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Удалить комментарий?</AlertDialogTitle>
                              <AlertDialogDescription>
                                Текст будет заменён на «(удалено)» — это видно всем участникам.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Отмена</AlertDialogCancel>
                              <AlertDialogAction
                                className="bg-destructive text-destructive-foreground hover:bg-status-danger-deep"
                                onClick={() => deleteMutation.mutate(comment.id)}
                              >
                                Удалить
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      ) : null}
                    </div>
                    <div
                      className={cn(
                        'mt-1 w-fit max-w-full whitespace-pre-wrap rounded-lg px-3 py-2 text-sm',
                        deleted
                          ? 'bg-muted italic text-muted-foreground'
                          : own
                            ? 'bg-primary-tint'
                            : 'bg-muted',
                      )}
                    >
                      {deleted ? '(удалено)' : comment.text}
                    </div>
                  </div>
                </motion.div>
              );
            })}
          </AnimatePresence>

          {pendingComments.map((pending) => (
            <div key={pending.key} className="flex items-start gap-2 opacity-60">
              <UserAvatar fullName={user?.full_name} size={28} tooltip={false} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">{user?.full_name}</span>
                  {pending.failed ? (
                    <>
                      <span className="text-xs text-status-danger-deep">не отправлено</span>
                      <Button
                        variant="link"
                        size="sm"
                        className="h-auto p-0 text-xs"
                        onClick={() => retry(pending)}
                      >
                        повторить
                      </Button>
                    </>
                  ) : (
                    <span className="text-xs text-muted-foreground" aria-live="polite">
                      отправляется…
                    </span>
                  )}
                </div>
                <div className="mt-1 w-fit max-w-full whitespace-pre-wrap rounded-lg bg-primary-tint px-3 py-2 text-sm">
                  {pending.text}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {canWrite ? (
        <div className="mt-4 flex items-end gap-2">
          <MentionsTextarea
            value={draft}
            onChange={setDraft}
            users={mentionUsers}
            rows={2}
            placeholder="Комментарий… @username — упомянуть коллегу"
            aria-label="Новый комментарий"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault();
                send();
              }
            }}
          />
          <Button
            size="icon"
            onClick={send}
            disabled={!draft.trim()}
            aria-label="Отправить комментарий"
          >
            <SendHorizontal aria-hidden="true" />
          </Button>
        </div>
      ) : null}
    </div>
  );
}
