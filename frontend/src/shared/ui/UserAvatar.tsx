import type { ReactNode } from 'react';
import { Avatar, AvatarFallback } from '@/shared/ui/avatar';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/shared/ui/tooltip';

/**
 * Аватар-инициалы ответственного (ux.md §7.1) на shadcn Avatar (§3.10):
 * детерминированный фон из 6 chart-цветов по hash имени — не «мигает»
 * между рендерами. Контракт прежний: fullName/size/tooltip.
 */

const AVATAR_COLORS = ['#1E56A0', '#C46A00', '#12917E', '#8250C8', '#C13B63', '#946300'];

function initialsOf(fullName: string): string {
  const parts = fullName.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  const first = parts[0]?.charAt(0) ?? '';
  const second = parts[1]?.charAt(0) ?? '';
  return (first + second).toUpperCase() || '?';
}

function colorOf(fullName: string): string {
  let hash = 0;
  for (const char of fullName) hash = (hash * 31 + char.charCodeAt(0)) % 997;
  return AVATAR_COLORS[hash % AVATAR_COLORS.length] ?? AVATAR_COLORS[0]!;
}

interface UserAvatarProps {
  fullName: string | null | undefined;
  size?: number;
  /** Показывать тултип с полным именем (для канбан-карточек). */
  tooltip?: boolean;
}

export function UserAvatar({ fullName, size = 24, tooltip = true }: UserAvatarProps): ReactNode {
  const name = fullName?.trim() || 'Не назначен';
  const avatar = (
    <Avatar style={{ width: size, height: size }}>
      <AvatarFallback
        className="font-semibold text-white"
        style={{
          backgroundColor: fullName ? colorOf(name) : '#B9C4D4',
          fontSize: Math.round(size * 0.42),
        }}
      >
        {fullName ? initialsOf(name) : '—'}
      </AvatarFallback>
    </Avatar>
  );
  if (!tooltip) return avatar;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex" aria-label={name}>
          {avatar}
        </span>
      </TooltipTrigger>
      <TooltipContent>{name}</TooltipContent>
    </Tooltip>
  );
}
