import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Bookmark, Loader2, Save, Settings2, Trash2 } from 'lucide-react';
import { useState, type ReactNode } from 'react';
import { useOnboarding } from '@/features/onboarding';
import { createPreset, deletePreset, updatePreset } from '@/shared/api/endpoints/presets';
import type { UiPreset, UiPresetState } from '@/shared/api/types';
import { toastError, toastSuccess } from '@/shared/lib/toast';
import { Button } from '@/shared/ui/button';
import { Checkbox } from '@/shared/ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { Input } from '@/shared/ui/input';
import { Label } from '@/shared/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/shared/ui/select';
import { StatusBadge } from './status-badge';

/**
 * Пресеты таблицы (ux.md §4.2) на новом ките (§3.2): Select применённого
 * пресета, «Сохранить как…» и «Управлять…». API-вызовы /ui/presets — те же.
 */

const NONE = '__none__';

export interface PresetsControlProps {
  screen: string;
  presets: UiPreset[];
  presetsAvailable: boolean;
  activePresetId: string | null;
  /** Текущее состояние таблицы — снапшот для сохранения. */
  currentState: () => UiPresetState;
  onApply: (preset: UiPreset) => void;
  onClear: () => void;
}

export function PresetsControl({
  screen,
  presets,
  presetsAvailable,
  activePresetId,
  currentState,
  onApply,
  onClear,
}: PresetsControlProps): ReactNode {
  const queryClient = useQueryClient();
  // Чек-лист онбординга §7.5: no-op без провайдера.
  const { completeChecklistItem } = useOnboarding();
  const [saveOpen, setSaveOpen] = useState(false);
  const [manageOpen, setManageOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveDefault, setSaveDefault] = useState(false);
  const [renameDrafts, setRenameDrafts] = useState<Record<string, string>>({});

  const invalidate = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['ui-presets', screen] });
  };

  const createMutation = useMutation({
    mutationFn: () =>
      createPreset({ screen, name: saveName.trim(), is_default: saveDefault, state: currentState() }),
    onSuccess: (preset) => {
      invalidate();
      onApply(preset);
      setSaveOpen(false);
      setSaveName('');
      setSaveDefault(false);
      toastSuccess('Пресет сохранён', `«${preset.name}» доступен в списке пресетов`);
      completeChecklistItem('save-preset');
    },
    onError: (error) => toastError(error, { title: 'Не удалось сохранить пресет' }),
  });

  const renameMutation = useMutation({
    mutationFn: ({ id, name }: { id: string; name: string }) => updatePreset(id, { name }),
    onSuccess: invalidate,
    onError: (error) => toastError(error, { title: 'Не удалось переименовать пресет' }),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deletePreset(id),
    onSuccess: (_data, id) => {
      invalidate();
      if (id === activePresetId) onClear();
    },
    onError: (error) => toastError(error, { title: 'Не удалось удалить пресет' }),
  });

  return (
    <div className="flex items-center gap-2">
      <Select
        disabled={!presetsAvailable}
        value={activePresetId ?? NONE}
        onValueChange={(id) => {
          if (id === NONE) {
            onClear();
            return;
          }
          const preset = presets.find((p) => p.id === id);
          if (preset) onApply(preset);
        }}
      >
        <SelectTrigger className="min-w-[160px]" aria-label="Пресет таблицы">
          <Bookmark className="size-4 text-muted-foreground" aria-hidden="true" />
          <SelectValue placeholder={presetsAvailable ? 'Пресеты' : 'Пресеты недоступны'} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={NONE}>Без пресета</SelectItem>
          {presets.map((preset) => (
            <SelectItem key={preset.id} value={preset.id}>
              {preset.is_default ? `${preset.name} (по умолчанию)` : preset.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="icon" aria-label="Действия с пресетами" disabled={!presetsAvailable}>
            <Settings2 aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onSelect={() => setSaveOpen(true)}>
            <Save aria-hidden="true" />
            Сохранить как пресет…
          </DropdownMenuItem>
          <DropdownMenuItem
            disabled={presets.length === 0}
            onSelect={() => {
              setRenameDrafts(Object.fromEntries(presets.map((p) => [p.id, p.name])));
              setManageOpen(true);
            }}
          >
            Управлять…
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className="sm:max-w-[420px]">
          <DialogHeader>
            <DialogTitle>Сохранить пресет</DialogTitle>
            <DialogDescription>
              Запомнит текущие фильтры, сортировку, колонки и размер страницы
            </DialogDescription>
          </DialogHeader>
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (saveName.trim()) createMutation.mutate();
            }}
          >
            <div className="grid gap-2">
              <Label htmlFor="preset-name">Название пресета</Label>
              <Input
                id="preset-name"
                autoFocus
                maxLength={80}
                placeholder="Например: Мои вузы ЦФО…"
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
              />
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <Checkbox
                checked={saveDefault}
                onCheckedChange={(checked) => setSaveDefault(checked === true)}
              />
              Применять по умолчанию при открытии экрана
            </label>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setSaveOpen(false)}>
                Отмена
              </Button>
              <Button type="submit" disabled={!saveName.trim() || createMutation.isPending}>
                {createMutation.isPending ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
                Сохранить
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={manageOpen} onOpenChange={setManageOpen}>
        <DialogContent className="sm:max-w-[480px]">
          <DialogHeader>
            <DialogTitle>Пресеты экрана</DialogTitle>
            <DialogDescription>Переименуйте или удалите сохранённые пресеты</DialogDescription>
          </DialogHeader>
          {presets.length === 0 ? (
            <p className="py-4 text-sm text-muted-foreground">Пресетов нет</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {presets.map((preset) => {
                const draft = renameDrafts[preset.id] ?? preset.name;
                return (
                  <li key={preset.id} className="flex items-center gap-2">
                    <Input
                      className="h-8 flex-1"
                      aria-label={`Название пресета ${preset.name}`}
                      value={draft}
                      onChange={(e) =>
                        setRenameDrafts((prev) => ({ ...prev, [preset.id]: e.target.value }))
                      }
                    />
                    {preset.is_default ? <StatusBadge status="draft" size="sm">по умолчанию</StatusBadge> : null}
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={
                        draft.trim() === preset.name ||
                        !draft.trim() ||
                        (renameMutation.isPending && renameMutation.variables?.id === preset.id)
                      }
                      onClick={() => renameMutation.mutate({ id: preset.id, name: draft.trim() })}
                    >
                      Переименовать
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="size-8 text-status-danger-deep hover:bg-status-danger-tint"
                      aria-label={`Удалить пресет ${preset.name}`}
                      disabled={deleteMutation.isPending && deleteMutation.variables === preset.id}
                      onClick={() => deleteMutation.mutate(preset.id)}
                    >
                      <Trash2 aria-hidden="true" />
                    </Button>
                  </li>
                );
              })}
            </ul>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
