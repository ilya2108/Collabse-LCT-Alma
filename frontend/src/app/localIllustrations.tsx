import type { ReactNode } from 'react';
import { Illustration } from '@/shared/illustrations';

/**
 * Совместимость WP3 (стадия Integrate): локальные сюжеты shell'а заменены
 * обёртками над общим реестром иллюстраций §4 (shared/illustrations, WP6).
 */

interface IllustrationProps {
  className?: string;
  /** Высота в px; ширина — по пропорции viewBox 240×160. */
  height?: number;
}

export function LoginHeroIllustration({ className, height = 200 }: IllustrationProps): ReactNode {
  return <Illustration name="login-hero" className={className} height={height} />;
}

export function ErrorBrokenIllustration({ className, height = 160 }: IllustrationProps): ReactNode {
  return <Illustration name="error-broken" className={className} height={height} />;
}

export function SearchEmptyIllustration({ className, height = 160 }: IllustrationProps): ReactNode {
  return <Illustration name="search-empty" className={className} height={height} />;
}

export function NotificationsEmptyIllustration({
  className,
  height = 96,
}: IllustrationProps): ReactNode {
  return <Illustration name="notifications-empty" className={className} height={height} />;
}
