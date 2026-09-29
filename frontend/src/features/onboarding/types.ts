import type { LucideIcon } from 'lucide-react';
import type { AppRole } from '@/shared/auth/roles';

/**
 * Типы фичи онбординга (redesign.md §7). Состояние хранится per-user в
 * /ui/presets (screen: "onboarding", name: "state") — §7.1.
 */

export type TourStatus = 'done' | 'skipped';

export interface OnboardingState {
  welcome_seen: boolean;
  tours: Record<string, TourStatus>;
  checklist: Record<string, boolean>;
  /** Чек-лист скрыт навсегда кнопкой «Скрыть» (§7.5). */
  checklist_hidden?: boolean;
  /** Рост версии конфига → предложить тур заново (§7.1). */
  version: number;
}

/** Текущая версия онбординга; при её росте welcome показывается снова. */
export const ONBOARDING_VERSION = 1;

export const DEFAULT_ONBOARDING_STATE: OnboardingState = {
  welcome_seen: false,
  tours: {},
  checklist: {},
  checklist_hidden: false,
  version: ONBOARDING_VERSION,
};

export interface TourStep {
  /** Значение data-tour целевого элемента (§7.3: стабильные id). */
  target: string;
  /** Маршрут, где живёт цель; тур сам делает navigate (§7.3). */
  route?: string;
  title: string;
  /** Текст ≤ 2 строк — дословно из §7.4. */
  text: string;
  placement?: 'bottom' | 'top' | 'right' | 'left';
}

export interface TourConfig {
  id: string;
  /** Название в меню перезапуска (§7.6). */
  title: string;
  /** Раздел (префикс маршрута) — для сортировки «текущий раздел — первым». */
  section: string;
  steps: TourStep[];
}

export interface ChecklistItemConfig {
  id: string;
  title: string;
  /** Куда ведёт клик по строке (§7.5). */
  route?: string;
  /** Пункт «Пройти тур» запускает тур вместо навигации. */
  startsTour?: boolean;
}

export interface WelcomeConfig {
  /** Один абзац по роли (§7.2). */
  paragraph: string;
  /** 3 плашки «что вы можете»: иконка + 3–4 слова. */
  highlights: Array<{ icon: LucideIcon; label: string }>;
  mainTourId: string;
}

export interface RoleOnboarding {
  role: AppRole;
  welcome: WelcomeConfig;
  tours: TourConfig[];
  checklist: ChecklistItemConfig[];
}
