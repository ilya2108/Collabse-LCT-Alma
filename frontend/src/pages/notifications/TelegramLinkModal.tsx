import { useMutation } from '@tanstack/react-query';
import { Check, Copy, Loader2, Send } from 'lucide-react';
import { motion } from 'motion/react';
import QRCode from 'qrcode';
import { useEffect, useState, type ReactNode } from 'react';
import { createTelegramLinkCode } from '@/shared/api/endpoints/notifications';
import { sseClient } from '@/shared/api/sse';
import type { TelegramLinkCode } from '@/shared/api/types';
import { springs } from '@/shared/lib/motion';
import { toastError, toastInfo, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import { Progress } from '@/shared/ui/progress';
import { Skeleton } from '@/shared/ui/skeleton';

/**
 * Модалка привязки Telegram (redesign.md §6.8, ux.md §13.3): QR крупно
 * (canvas qrcode — закрытый контур), код 6 цифр 28px tabular с кнопкой Copy,
 * таймер TTL тонкой полоской Progress. По SSE `telegram.linked` контент
 * сменяется галкой в success-круге (scale springs.bouncy) и модалка
 * закрывается сама через 1.2 с.
 */

interface TelegramLinkModalProps {
  open: boolean;
  onClose: () => void;
  /** Вызывается при успешной привязке (перечитать настройки). */
  onLinked: () => void;
}

function formatCountdown(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function TelegramLinkModal({ open, onClose, onLinked }: TelegramLinkModalProps): ReactNode {
  const [linkData, setLinkData] = useState<TelegramLinkCode | null>(null);
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [ttlTotal, setTtlTotal] = useState(600);
  const [linked, setLinked] = useState(false);

  const codeMutation = useMutation({
    mutationFn: createTelegramLinkCode,
    meta: { silent: true },
    onSuccess: (data) => {
      setLinkData(data);
      setLinked(false);
      const total = Math.max(
        1,
        Math.floor((new Date(data.expires_at).getTime() - Date.now()) / 1000),
      );
      setTtlTotal(total);
    },
    onError: (error) => toastError(error, { title: 'Не удалось получить код привязки' }),
  });

  // запрос кода при каждом открытии (повторный запрос инвалидирует старый код)
  useEffect(() => {
    if (open) {
      setLinkData(null);
      setQrDataUrl(null);
      setLinked(false);
      codeMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // QR по deep-link
  useEffect(() => {
    if (!linkData) return;
    let cancelled = false;
    QRCode.toDataURL(linkData.deep_link, { width: 220, margin: 1 })
      .then((url) => {
        if (!cancelled) setQrDataUrl(url);
      })
      .catch(() => {
        if (!cancelled) setQrDataUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [linkData]);

  // обратный отсчёт TTL
  useEffect(() => {
    if (!linkData || linked) return;
    const update = (): void => {
      const left = Math.max(
        0,
        Math.floor((new Date(linkData.expires_at).getTime() - Date.now()) / 1000),
      );
      setSecondsLeft(left);
    };
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [linkData, linked]);

  // SSE: бот привязал аккаунт → галка и автозакрытие через 1.2 с (§6.8)
  useEffect(() => {
    if (!open) return;
    return sseClient.subscribe('telegram.linked', () => {
      setLinked(true);
      onLinked();
      window.setTimeout(onClose, 1200);
    });
  }, [open, onLinked, onClose]);

  const expired = Boolean(linkData) && secondsLeft <= 0 && !linked;

  let body: ReactNode;
  if (linked) {
    body = (
      <div className="flex flex-col items-center gap-3 py-6 text-center">
        <motion.span
          initial={{ scale: 0 }}
          animate={{ scale: 1 }}
          transition={springs.bouncy}
          className="flex size-16 items-center justify-center rounded-full bg-status-success-tint text-status-success-deep"
        >
          <Check className="size-8" aria-hidden="true" />
        </motion.span>
        <div className="text-base font-semibold">Telegram привязан</div>
        <p className="text-sm text-muted-foreground">Уведомления будут приходить боту</p>
      </div>
    );
  } else if (codeMutation.isPending || !linkData) {
    body = (
      <div className="flex flex-col items-center gap-4 py-4" aria-hidden="true">
        <Skeleton className="size-[220px] shimmer rounded-md" />
        <Skeleton className="h-9 w-40 shimmer" />
        <Skeleton className="h-4 w-56 shimmer" />
      </div>
    );
  } else {
    body = (
      <div className="flex flex-col items-center gap-3">
        {qrDataUrl ? (
          <img
            src={qrDataUrl}
            alt="QR-код привязки Telegram"
            width={220}
            height={220}
            className="rounded-md"
          />
        ) : (
          <div className="flex size-[220px] items-center justify-center">
            <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
          </div>
        )}
        <div className="flex items-center gap-2">
          <span className="text-[28px] font-semibold leading-9 tracking-[0.3em] tabular">
            {linkData.code}
          </span>
          <Button
            variant="outline"
            size="icon"
            aria-label="Скопировать код"
            onClick={() => {
              void navigator.clipboard
                .writeText(linkData.code)
                .then(() => toastSuccess('Скопировано'))
                .catch(() => toastInfo('Скопируйте код вручную'));
            }}
          >
            <Copy aria-hidden="true" />
          </Button>
        </div>
        {expired ? (
          <div className="flex flex-col items-center gap-2">
            <p className="text-sm text-status-danger-deep">Срок действия кода истёк</p>
            <Button onClick={() => codeMutation.mutate()}>Получить новый код</Button>
          </div>
        ) : (
          <>
            <div className="w-full space-y-1">
              <Progress value={(secondsLeft / ttlTotal) * 100} className="h-1" />
              <p className="text-center text-xs text-muted-foreground tabular" aria-live="polite">
                Код действителен {formatCountdown(secondsLeft)}
              </p>
            </div>
            <Button asChild>
              <a href={linkData.deep_link} target="_blank" rel="noreferrer">
                <Send aria-hidden="true" />
                Открыть Telegram
              </a>
            </Button>
            <p className="text-center text-xs text-muted-foreground">
              Отсканируйте QR или отправьте боту команду{' '}
              <code className="rounded-sm bg-muted px-1 py-0.5 font-mono">
                /start {linkData.code}
              </code>
              . Модалка закроется сама после привязки.
            </p>
          </>
        )}
      </div>
    );
  }

  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-[380px]">
        <DialogHeader>
          <DialogTitle>Привязка Telegram</DialogTitle>
          <DialogDescription className="sr-only">
            Привяжите Telegram-бота по одноразовому коду
          </DialogDescription>
        </DialogHeader>
        {body}
      </DialogContent>
    </Dialog>
  );
}
