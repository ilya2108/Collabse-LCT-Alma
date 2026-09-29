/**
 * Публичный API фичи онбординга (redesign.md §7):
 * - <OnboardingProvider/> — монтируется внутри Router вокруг layout
 *   (стадия Integrate);
 * - useOnboarding() — контекст (безопасен без провайдера: no-op);
 * - <RestartTourButton/> — кнопка CircleHelp в слот топбара WP3 (Integrate);
 * - <ChecklistCard/> — карточка «Начало работы» на дашборд (Integrate);
 * - data-tour-атрибуты целей — список в §7.4 (чужие экраны размечает
 *   Integrate; WP6 разметил свои: talent-funnel, talent-pii, notif-thresholds).
 */
export { OnboardingProvider, useOnboarding } from './OnboardingProvider';
export { RestartTourButton } from './RestartTourButton';
export { ChecklistCard } from './ChecklistCard';
export { WelcomeDialog } from './WelcomeDialog';
export { SpotlightTour } from './SpotlightTour';
export { ROLE_ONBOARDING, toursForRole } from './tours';
export type {
  OnboardingState,
  TourConfig,
  TourStep,
  ChecklistItemConfig,
  RoleOnboarding,
} from './types';
