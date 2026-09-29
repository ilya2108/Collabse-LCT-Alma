import type { ComponentType, ReactNode, SVGProps } from 'react';
import { cn } from '@/shared/lib/cn';
import {
  BoardEmptyIllustration,
  Error403Illustration,
  Error404Illustration,
  ErrorBrokenIllustration,
  ImportDoneIllustration,
  ImportDropIllustration,
  LoginHeroIllustration,
  NotificationsEmptyIllustration,
  OnboardingWelcomeIllustration,
  RegistryEmptyIllustration,
  SearchEmptyIllustration,
  StudentsEmptyIllustration,
} from './sujets';

/**
 * Реестр иллюстраций (redesign.md §3.10, §4): <Illustration name="…"/> —
 * инлайн-SVG, aria-hidden, размеры заданы (нет CLS). Размеры употребления §4.1:
 * empty-state 160–200px, compact 96px, login/onboarding 200–240px высоты.
 */

const REGISTRY = {
  'board-empty': BoardEmptyIllustration,
  'registry-empty': RegistryEmptyIllustration,
  'students-empty': StudentsEmptyIllustration,
  'search-empty': SearchEmptyIllustration,
  'import-drop': ImportDropIllustration,
  'import-done': ImportDoneIllustration,
  'login-hero': LoginHeroIllustration,
  'error-broken': ErrorBrokenIllustration,
  'error-403': Error403Illustration,
  'error-404': Error404Illustration,
  'onboarding-welcome': OnboardingWelcomeIllustration,
  'notifications-empty': NotificationsEmptyIllustration,
} satisfies Record<string, ComponentType<SVGProps<SVGSVGElement>>>;

export type IllustrationName = keyof typeof REGISTRY;

export interface IllustrationProps {
  name: IllustrationName;
  className?: string;
  /** Высота в px; ширина — по пропорции viewBox 240×160. */
  height?: number;
}

export function Illustration({ name, className, height = 160 }: IllustrationProps): ReactNode {
  const Sujet = REGISTRY[name];
  const width = Math.round((height * 240) / 160);
  return (
    <Sujet
      className={cn('shrink-0', className)}
      width={width}
      height={height}
      aria-hidden="true"
      focusable="false"
    />
  );
}

export {
  BoardEmptyIllustration,
  RegistryEmptyIllustration,
  StudentsEmptyIllustration,
  SearchEmptyIllustration,
  ImportDropIllustration,
  ImportDoneIllustration,
  LoginHeroIllustration,
  ErrorBrokenIllustration,
  Error403Illustration,
  Error404Illustration,
  OnboardingWelcomeIllustration,
  NotificationsEmptyIllustration,
};
