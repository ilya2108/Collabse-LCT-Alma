import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * Склейка tailwind-классов (shadcn-конвенция): clsx для условий,
 * tailwind-merge для разрешения конфликтов ('px-2 px-4' → 'px-4').
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
