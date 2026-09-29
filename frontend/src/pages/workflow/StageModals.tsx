import { ArrowRightLeft, FileText, TriangleAlert } from 'lucide-react';
import { useEffect, useId, useState, type ReactNode } from 'react';
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/shared/ui/alert-dialog';
import { Alert, AlertDescription, AlertTitle } from '@/shared/ui/alert';
import { ConfirmDialog } from '@/shared/ui/confirm-dialog';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import { RadioGroup, RadioGroupItem } from '@/shared/ui/radio-group';
import type { MigrateOption } from './model';

/**
 * Красные сценарии конструктора (redesign.md §3.8, §6.6): переименование и
 * удаление этапа — ConfirmDialog с посимвольным вводом текущего названия;
 * введённая строка уходит в операцию полем `confirm_name` и повторно
 * сверяется на бэке. Удаление — c фактами-плашками и radio этапа миграции.
 */

interface RenameStageModalProps {
  open: boolean;
  /** Текущее (живое) название этапа — эталон для подтверждения. */
  baseName: string;
  /** Текущее название в черновике (могли уже переименовать). */
  draftName: string;
  onConfirm: (newName: string, confirmName: string) => void;
  onCancel: () => void;
}

export function RenameStageModal({
  open,
  baseName,
  draftName,
  onConfirm,
  onCancel,
}: RenameStageModalProps): ReactNode {
  const inputId = useId();
  const [newName, setNewName] = useState(draftName);
  const [nameError, setNameError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setNewName(draftName);
      setNameError(null);
    }
  }, [open, draftName]);

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => (!next ? onCancel() : undefined)}
      tone="danger"
      title={`Переименование этапа «${baseName}»`}
      facts={[
        { icon: TriangleAlert, label: 'применится после согласования администратора' },
        { icon: FileText, label: 'заявки остаются на месте' },
      ]}
      body={
        <div className="space-y-1.5">
          <Label htmlFor={inputId}>Новое название</Label>
          <Input
            id={inputId}
            value={newName}
            maxLength={80}
            autoComplete="off"
            placeholder="Например: Первый контакт…"
            aria-invalid={nameError ? true : undefined}
            onChange={(event) => {
              setNewName(event.target.value);
              setNameError(null);
            }}
          />
          {nameError ? (
            <p className="text-xs text-status-danger-deep" role="alert">
              {nameError}
            </p>
          ) : null}
        </div>
      }
      confirmWord={baseName}
      confirmLabel="Переименовать (в пакет изменений)"
      onConfirm={(typed) => {
        const trimmed = newName.trim();
        if (trimmed.length === 0) {
          setNameError('Укажите новое название');
          return;
        }
        if (trimmed === baseName) {
          setNameError('Название не изменилось — переименование не требуется');
          return;
        }
        onConfirm(trimmed, typed ?? '');
      }}
    />
  );
}

interface DeleteStageModalProps {
  open: boolean;
  baseName: string;
  /** Заявок сейчас на этапе (клиентская оценка; точный impact — при отправке). */
  requestsCount: number | null;
  approxCount: boolean;
  incomingCount: number;
  outgoingCount: number;
  options: MigrateOption[];
  defaultId: string | null;
  onConfirm: (migrateToStatusId: string, confirmName: string) => void;
  onCancel: () => void;
}

export function DeleteStageModal({
  open,
  baseName,
  requestsCount,
  approxCount,
  incomingCount,
  outgoingCount,
  options,
  defaultId,
  onConfirm,
  onCancel,
}: DeleteStageModalProps): ReactNode {
  const [migrateTo, setMigrateTo] = useState<string | null>(defaultId);
  useEffect(() => {
    if (open) setMigrateTo(defaultId);
  }, [open, defaultId]);

  const countLabel =
    requestsCount === null ? '…' : `${approxCount ? '≈' : ''}${requestsCount}`;

  // Нет цели миграции — удалить нельзя, только объяснение (ноль тупиков).
  if (options.length === 0) {
    return (
      <AlertDialog open={open} onOpenChange={(next) => (!next ? onCancel() : undefined)}>
        <AlertDialogContent className="max-w-lg">
          <AlertDialogHeader>
            <AlertDialogTitle>Удаление этапа «{baseName}» невозможно</AlertDialogTitle>
            <AlertDialogDescription>
              Целью переноса заявок может быть только соседний (связанный переходом)
              нетерминальный этап текущей схемы. Сначала добавьте или освободите соседний этап.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Понятно</AlertDialogCancel>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    );
  }

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={(next) => (!next ? onCancel() : undefined)}
      tone="danger"
      title={`Удаление этапа «${baseName}»`}
      facts={[
        { icon: FileText, label: <>заявок: {countLabel}</> },
        {
          icon: ArrowRightLeft,
          label: (
            <>
              переходов: {incomingCount} вх / {outgoingCount} исх — будут удалены
            </>
          ),
        },
      ]}
      body={
        <div className="space-y-3">
          <Alert variant="destructive">
            <TriangleAlert aria-hidden="true" />
            <AlertTitle>Применится после согласования администратором</AlertTitle>
            <AlertDescription>
              Заявки будут атомарно перенесены на выбранный этап, каждой добавится запись в
              историю, ответственные получат уведомления.
            </AlertDescription>
          </Alert>
          <fieldset>
            <legend className="mb-2 text-sm font-medium">Куда перенести заявки</legend>
            <RadioGroup
              value={migrateTo ?? undefined}
              onValueChange={(value) => setMigrateTo(value)}
              className="gap-2"
            >
              {options.map((option) => (
                <label
                  key={option.stage.id}
                  className="flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm transition-[border-color,background-color] duration-150 has-[[data-state=checked]]:border-primary has-[[data-state=checked]]:bg-primary-tint"
                >
                  <RadioGroupItem value={option.stage.id} />
                  <span className="min-w-0 flex-1 truncate">«{option.stage.name}»</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {option.direction === 'prev' ? '← предыдущий' : '→ следующий'}
                    {option.stage.id === defaultId ? ' · рекомендовано' : ''}
                  </span>
                </label>
              ))}
            </RadioGroup>
          </fieldset>
        </div>
      }
      confirmWord={baseName}
      confirmLabel="Удалить этап (в пакет изменений)"
      onConfirm={(typed) => {
        if (migrateTo) onConfirm(migrateTo, typed ?? '');
      }}
    />
  );
}
