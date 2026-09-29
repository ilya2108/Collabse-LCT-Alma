import { CircleHelp } from 'lucide-react';
import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { Button } from '@/shared/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/shared/ui/dropdown-menu';
import { useOnboarding } from './OnboardingProvider';

/**
 * Кнопка перезапуска тура в шапке (redesign.md §7.6, слот в топбаре — WP3;
 * подключает стадия Integrate). Меню: туры роли (текущий раздел — первым),
 * «Показать чек-лист» (если скрыт), «Приветственный экран». Перезапуск
 * не сбрасывает чек-лист.
 */
export function RestartTourButton(): ReactNode {
  const location = useLocation();
  const { ready, tours, state, startTour, showChecklist, showWelcome } = useOnboarding();

  if (!ready || tours.length === 0) return null;

  const sorted = [...tours].sort((a, b) => {
    const aCurrent = location.pathname.startsWith(a.section) ? 0 : 1;
    const bCurrent = location.pathname.startsWith(b.section) ? 0 : 1;
    return aCurrent - bCurrent;
  });

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Помощь и туры">
          <CircleHelp aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-56">
        <DropdownMenuLabel className="text-xs text-muted-foreground">
          Пройти тур по разделу
        </DropdownMenuLabel>
        {sorted.map((tour) => (
          <DropdownMenuItem key={tour.id} onSelect={() => startTour(tour.id)}>
            {tour.title}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        {state.checklist_hidden ? (
          <DropdownMenuItem onSelect={showChecklist}>Показать контрольный список</DropdownMenuItem>
        ) : null}
        <DropdownMenuItem onSelect={showWelcome}>Приветственный экран</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
