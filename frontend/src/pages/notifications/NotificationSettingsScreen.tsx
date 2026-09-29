import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  ArrowRightLeft,
  BellRing,
  Flame,
  GraduationCap,
  Mail,
  MessageCircle,
  MessageSquare,
  Plug,
  Send,
  TriangleAlert,
  UserRound,
  Workflow,
  type LucideIcon,
} from 'lucide-react';
import { motion } from 'motion/react';
import { useState, type ReactNode } from 'react';
import { useOnboarding } from '@/features/onboarding';
import {
  getNotificationSettings,
  listNotificationChannels,
  putNotificationSettings,
  sendTestNotification,
  unlinkTelegram,
} from '@/shared/api/endpoints/notifications';
import { getAdminSettings, putAdminSettings } from '@/shared/api/endpoints/admin';
import type {
  NotificationChannelCode,
  NotificationSettings,
} from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import { Illustration } from '@/shared/illustrations';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { toastError, toastSuccess } from '@/shared/lib/toast';
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
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/shared/ui/collapsible';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { Switch } from '@/shared/ui/switch';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/shared/ui/table';
import { EscalationDiagram } from './EscalationDiagram';
import { ErrorState, LoadingBlock, PageHeader, StatusBadge } from './local';
import { TelegramLinkModal } from './TelegramLinkModal';

/**
 * Настройки нотификаций (redesign.md §6.8, ux.md §13): карточки каналов с
 * иконками в tint-квадратах, матрица подписок компактной таблицей со sticky-
 * шапкой, «Правила и пороги» с мини-SVG-схемой эскалации вместо текстовой
 * простыни. Каждое изменение сохраняется сразу (PUT с version, optimistic).
 */

/** Русские подписи типов событий из контракта §9.1 + иконки типов (§6.8). */
const EVENT_META: Record<string, { label: string; icon: LucideIcon }> = {
  'request.transitioned': { label: 'Переход заявки по этапу', icon: ArrowRightLeft },
  'request.assigned': { label: 'Мне назначили заявку', icon: UserRound },
  'request.stuck': { label: 'Заявка зависла', icon: Flame },
  'request.comment_added': { label: 'Комментарий или упоминание', icon: MessageSquare },
  'workflow.changed': { label: 'Схема процесса изменена', icon: Workflow },
  'student.talent_pool_added': { label: 'Изменения в пуле талантов', icon: GraduationCap },
  'integration.lead_received': { label: 'Получен лид из CMS', icon: Plug },
};

/** События только для управляющих ролей (ux.md §13.1). */
const MANAGER_ONLY_EVENTS = new Set(['workflow.changed']);

interface EventRow {
  event: string;
  label: string;
  icon: LucideIcon;
  enabled: boolean;
  onlyMine: boolean | undefined;
}

/** Карточка канала: иконка в tint-квадрате + статус-бейдж (§6.8). */
function ChannelCard({
  icon: Icon,
  title,
  badge,
  children,
}: {
  icon: LucideIcon;
  title: string;
  badge: ReactNode;
  children: ReactNode;
}): ReactNode {
  return (
    <motion.section
      variants={staggerItem}
      className="flex flex-col gap-3 rounded-lg border bg-card p-5 shadow-card"
      aria-label={title}
    >
      <div className="flex items-center gap-3">
        <span className="flex size-10 shrink-0 items-center justify-center rounded-md bg-primary-tint text-primary">
          <Icon className="size-5" aria-hidden="true" />
        </span>
        <span className="min-w-0 flex-1 truncate text-base font-semibold">{title}</span>
      </div>
      <div>{badge}</div>
      <div className="flex flex-1 flex-col gap-3">{children}</div>
    </motion.section>
  );
}

function SwitchRow({
  id,
  checked,
  onChange,
  label,
}: {
  id: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
}): ReactNode {
  return (
    <label htmlFor={id} className="flex cursor-pointer items-center gap-2 text-sm">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <span className="text-muted-foreground">{label}</span>
    </label>
  );
}

export function NotificationSettingsScreen(): ReactNode {
  const { user, hasRole } = useAuth();
  const queryClient = useQueryClient();
  const { completeChecklistItem } = useOnboarding();
  const [linkModalOpen, setLinkModalOpen] = useState(false);
  const [maxAddress, setMaxAddress] = useState<string | null>(null);
  const [thresholdDraft, setThresholdDraft] = useState<string | null>(null);

  const settingsQuery = useQuery({
    queryKey: ['notification-settings'],
    queryFn: ({ signal }) => getNotificationSettings(signal),
  });
  const channelsQuery = useQuery({
    queryKey: ['notification-channels'],
    queryFn: ({ signal }) => listNotificationChannels(signal),
    staleTime: 60_000,
    retry: 1,
  });

  const settings = settingsQuery.data ?? null;
  const channelStatus = (code: NotificationChannelCode): 'active' | 'stub' | 'down' | null =>
    channelsQuery.data?.items.find((c) => c.code === code)?.status ?? null;

  const saveMutation = useMutation({
    mutationFn: (next: NotificationSettings) => putNotificationSettings(next),
    meta: { silent: true },
    onSuccess: (saved) => {
      queryClient.setQueryData(['notification-settings'], saved);
    },
    onError: (error) => {
      toastError(error, { title: 'Не удалось сохранить настройки' });
      void queryClient.invalidateQueries({ queryKey: ['notification-settings'] });
    },
  });

  const patchSettings = (
    patch: (current: NotificationSettings) => NotificationSettings,
  ): void => {
    if (!settings) return;
    const next = patch(settings);
    queryClient.setQueryData(['notification-settings'], next); // optimistic
    saveMutation.mutate(next);
  };

  const setChannelEnabled = (code: NotificationChannelCode, enabled: boolean): void => {
    patchSettings((current) => ({
      ...current,
      channels: {
        ...current.channels,
        [code]: { ...(current.channels[code] ?? {}), enabled },
      },
    }));
  };

  const testMutation = useMutation({
    mutationFn: (channel: NotificationChannelCode) => sendTestNotification(channel),
    meta: { silent: true },
    onSuccess: (_, channel) =>
      toastSuccess(
        'Тестовое сообщение отправлено',
        channel === 'telegram'
          ? 'Проверьте чат с ботом'
          : 'Отправка заглушечного канала видна в журнале notification-service',
      ),
    onError: (error) => toastError(error, { title: 'Тестовая отправка не удалась' }),
  });

  const unlinkMutation = useMutation({
    mutationFn: unlinkTelegram,
    meta: { silent: true },
    onSuccess: () => {
      toastSuccess('Telegram отвязан');
      void queryClient.invalidateQueries({ queryKey: ['notification-settings'] });
    },
    onError: (error) => toastError(error, { title: 'Не удалось отвязать Telegram' }),
  });

  // --- Пороги и эскалация (admin: GET/PUT /admin/settings §10.3) --------------
  const isAdmin = hasRole('admin');
  const isManager = hasRole('admin', 'head_kam');
  const adminSettingsQuery = useQuery({
    queryKey: ['admin-settings'],
    queryFn: ({ signal }) => getAdminSettings(signal),
    enabled: isAdmin,
  });
  const adminSaveMutation = useMutation({
    mutationFn: putAdminSettings,
    meta: { silent: true },
    onSuccess: (saved) => {
      queryClient.setQueryData(['admin-settings'], saved);
      toastSuccess('Пороги сохранены');
      completeChecklistItem('thresholds');
    },
    onError: (error) => {
      toastError(error, { title: 'Не удалось сохранить пороги' });
      void queryClient.invalidateQueries({ queryKey: ['admin-settings'] });
    },
  });

  if (settingsQuery.isLoading) return <LoadingBlock rows={10} />;
  if (settingsQuery.isError || !settings) {
    return (
      <ErrorState
        error={settingsQuery.error}
        title="Не удалось загрузить настройки"
        onRetry={() => void settingsQuery.refetch()}
      />
    );
  }

  const telegram = settings.channels.telegram;
  const email = settings.channels.email;
  const max = settings.channels.max;
  const inApp = settings.channels.in_app;

  const telegramDown = channelStatus('telegram') === 'down';
  const tgUsername = user?.telegram?.tg_username;

  const eventRows: EventRow[] = Object.entries(settings.events)
    .filter(([event]) => !MANAGER_ONLY_EVENTS.has(event) || isManager)
    .map(([event, setting]) => ({
      event,
      label: EVENT_META[event]?.label ?? event,
      icon: EVENT_META[event]?.icon ?? BellRing,
      enabled: setting.enabled,
      onlyMine: setting.only_mine,
    }));

  const channelCards = (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="visible"
      className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4"
    >
      <ChannelCard
        icon={Send}
        title="Telegram"
        badge={
          telegram?.linked ? (
            <StatusBadge status="success">
              Привязан{tgUsername ? `: @${tgUsername}` : ''}
            </StatusBadge>
          ) : (
            <StatusBadge status="draft">Не привязан</StatusBadge>
          )
        }
      >
        {telegramDown ? (
          <p className="flex items-center gap-1.5 text-xs text-status-warning-deep">
            <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
            Сервис уведомлений недоступен
          </p>
        ) : null}
        {telegram?.linked ? (
          <>
            <SwitchRow
              id="tg-enabled"
              checked={telegram.enabled}
              onChange={(checked) => setChannelEnabled('telegram', checked)}
              label="Получать уведомления"
            />
            <div className="mt-auto flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => testMutation.mutate('telegram')}
                disabled={testMutation.isPending}
              >
                Тест
              </Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-status-danger-deep hover:text-status-danger-deep"
                    disabled={unlinkMutation.isPending}
                  >
                    Отвязать
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Отвязать Telegram?</AlertDialogTitle>
                    <AlertDialogDescription>
                      Бот перестанет присылать уведомления и отвечать на команды.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Отмена</AlertDialogCancel>
                    <AlertDialogAction
                      className="bg-destructive text-destructive-foreground hover:bg-status-danger-deep"
                      onClick={() => unlinkMutation.mutate()}
                    >
                      Отвязать
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>
          </>
        ) : (
          <>
            <div className="flex justify-center">
              <Illustration name="notifications-empty" height={96} />
            </div>
            <p className="text-center text-xs text-muted-foreground">
              Бот пришлёт события и ответит на команды /my и /stuck
            </p>
            <Button
              className="mt-auto"
              onClick={() => setLinkModalOpen(true)}
              disabled={telegramDown}
            >
              Привязать Telegram
            </Button>
          </>
        )}
      </ChannelCard>
      <ChannelCard
        icon={Mail}
        title="Email"
        badge={<StatusBadge status="warning">Exchange — заглушка</StatusBadge>}
      >
        <p className="text-xs text-muted-foreground">Адрес из Keycloak:</p>
        <p className="truncate text-sm">{email?.address ?? user?.email ?? '—'}</p>
        <SwitchRow
          id="email-enabled"
          checked={email?.enabled ?? false}
          onChange={(checked) => setChannelEnabled('email', checked)}
          label="Получать письма"
        />
        <div className="mt-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={() => testMutation.mutate('email')}
            disabled={testMutation.isPending}
          >
            Тест
          </Button>
        </div>
      </ChannelCard>
      <ChannelCard
        icon={MessageCircle}
        title="Max"
        badge={<StatusBadge status="warning">заглушка</StatusBadge>}
      >
        <div className="space-y-1.5">
          <Label htmlFor="max-address" className="text-xs text-muted-foreground">
            Идентификатор Max
          </Label>
          <Input
            id="max-address"
            placeholder="Идентификатор Max…"
            autoComplete="off"
            value={maxAddress ?? max?.address ?? ''}
            onChange={(e) => setMaxAddress(e.target.value)}
            onBlur={() => {
              const address = (maxAddress ?? '').trim();
              if (maxAddress === null || address === (max?.address ?? '')) return;
              patchSettings((current) => ({
                ...current,
                channels: {
                  ...current.channels,
                  max: { ...(current.channels.max ?? { enabled: false }), address: address || null },
                },
              }));
            }}
          />
        </div>
        <SwitchRow
          id="max-enabled"
          checked={max?.enabled ?? false}
          onChange={(checked) => setChannelEnabled('max', checked)}
          label="Получать уведомления"
        />
        <div className="mt-auto">
          <Button
            variant="outline"
            size="sm"
            onClick={() => testMutation.mutate('max')}
            disabled={testMutation.isPending}
          >
            Тест
          </Button>
        </div>
      </ChannelCard>
      <ChannelCard
        icon={BellRing}
        title="В приложении"
        badge={<StatusBadge status="success">активен</StatusBadge>}
      >
        <p className="text-xs text-muted-foreground">
          Колокольчик в шапке и лента уведомлений
        </p>
        <SwitchRow
          id="inapp-enabled"
          checked={inApp?.enabled ?? true}
          onChange={(checked) => setChannelEnabled('in_app', checked)}
          label="Показывать уведомления"
        />
      </ChannelCard>
    </motion.div>
  );

  const eventsMatrix = (
    <section className="rounded-lg border bg-card shadow-card" aria-label="Подписки на события">
      <h2 className="border-b px-5 py-3 text-base font-semibold">Подписки на события</h2>
      <div className="max-h-96 overflow-y-auto">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-muted">
            <TableRow>
              <TableHead scope="col">Событие</TableHead>
              <TableHead scope="col" className="w-28">
                Получать
              </TableHead>
              <TableHead scope="col" className="w-40">
                Только мои заявки
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {eventRows.map((row) => {
              const Icon = row.icon;
              return (
                <TableRow key={row.event}>
                  <TableCell>
                    <span className="flex items-center gap-2.5">
                      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
                        <Icon className="size-3.5" aria-hidden="true" />
                      </span>
                      {row.label}
                    </span>
                  </TableCell>
                  <TableCell>
                    <Switch
                      aria-label={`Получать: ${row.label}`}
                      checked={row.enabled}
                      onCheckedChange={(checked) =>
                        patchSettings((current) => ({
                          ...current,
                          events: {
                            ...current.events,
                            [row.event]: { ...current.events[row.event], enabled: checked },
                          },
                        }))
                      }
                    />
                  </TableCell>
                  <TableCell>
                    {row.onlyMine === undefined ? (
                      <span className="text-muted-foreground">—</span>
                    ) : (
                      <Switch
                        aria-label={`Только мои заявки: ${row.label}`}
                        checked={row.onlyMine}
                        disabled={!row.enabled}
                        onCheckedChange={(checked) =>
                          patchSettings((current) => ({
                            ...current,
                            events: {
                              ...current.events,
                              [row.event]: { ...current.events[row.event], only_mine: checked },
                            },
                          }))
                        }
                      />
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </section>
  );

  const adminSettings = adminSettingsQuery.data;
  const commitThreshold = (): void => {
    if (!adminSettings || thresholdDraft === null) return;
    const value = Number.parseInt(thresholdDraft, 10);
    setThresholdDraft(null);
    if (!Number.isFinite(value) || value < 1 || value > 60) return;
    if (value === adminSettings.stuck_threshold_days) return;
    adminSaveMutation.mutate({ ...adminSettings, stuck_threshold_days: value });
  };

  const thresholdSection = isManager ? (
    <section
      className="space-y-4 rounded-lg border bg-card p-5 shadow-card"
      aria-label="Правила и пороги зависания"
      data-tour="notif-thresholds"
    >
      <h2 className="text-base font-semibold">Правила и пороги «зависания»</h2>
      {isAdmin ? (
        adminSettingsQuery.isLoading ? (
          <LoadingBlock rows={3} />
        ) : adminSettingsQuery.isError ? (
          <ErrorState
            error={adminSettingsQuery.error}
            title="Не удалось загрузить пороги"
            onRetry={() => void adminSettingsQuery.refetch()}
            compact
          />
        ) : adminSettings ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Label htmlFor="stuck-days">Заявка считается зависшей после</Label>
              <Input
                id="stuck-days"
                type="number"
                inputMode="numeric"
                min={1}
                max={60}
                className="w-20 tabular"
                value={thresholdDraft ?? String(adminSettings.stuck_threshold_days)}
                onChange={(e) => setThresholdDraft(e.target.value)}
                onBlur={commitThreshold}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') commitThreshold();
                }}
              />
              <span>дней без смены этапа</span>
            </div>
            <p className="text-xs text-muted-foreground">
              Порог можно переопределить на конкретном этапе в конструкторе workflow.
            </p>
            <SwitchRow
              id="escalate"
              checked={adminSettings.stuck_escalate_to_manager}
              onChange={(checked) =>
                adminSaveMutation.mutate({
                  ...adminSettings,
                  stuck_escalate_to_manager: checked,
                })
              }
              label="Эскалация: при 1.5× порога уведомить руководителя КАМа"
            />
          </div>
        ) : null
      ) : (
        <p className="text-sm text-muted-foreground">
          Пороги настраивает администратор. По умолчанию — 14 дней без смены этапа
          (каскад: правило → этап → процесс → общая настройка).
        </p>
      )}
      <EscalationDiagram days={isAdmin ? (adminSettings?.stuck_threshold_days ?? null) : null} />
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="text-muted-foreground">
            Как это работает технически
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <p className="px-3 pt-2 text-xs text-muted-foreground">
            Проверку зависших заявок запускает планировщик Kubernetes CronJob каждый час
            (POST /internal/jobs/check-stuck-requests). Порог считается каскадом:
            правило → этап → процесс → общая настройка. Дубли уведомлений подавляются
            ключом дедупликации в outbox-таблице.
          </p>
        </CollapsibleContent>
      </Collapsible>
    </section>
  ) : null;

  return (
    <>
      <PageHeader
        title="Настройки нотификаций"
        subtitle="Каналы, подписки на события и пороги «зависания» заявок"
      />
      <div className="space-y-4">
        {channelCards}
        {eventsMatrix}
        {thresholdSection}
      </div>
      <TelegramLinkModal
        open={linkModalOpen}
        onClose={() => setLinkModalOpen(false)}
        onLinked={() => {
          completeChecklistItem('link-telegram');
          void queryClient.invalidateQueries({ queryKey: ['notification-settings'] });
        }}
      />
    </>
  );
}
