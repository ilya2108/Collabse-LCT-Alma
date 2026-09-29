import { useQuery } from '@tanstack/react-query';
import { getFlags } from '@/shared/api/endpoints/admin';
import type { FlagsMap } from '@/shared/api/types';
import { useSseInvalidate } from './useSseInvalidate';

/**
 * Живые фичефлаги (api-contract.md §2.2, ux.md §14.3): `GET /flags` +
 * SSE `flags.updated` → элементы под флагом монтируются/скрываются без
 * перезагрузки страницы. При недоступности ручки все флаги считаются
 * выключенными (деградация без падения UI).
 */
export function useFlags(): { flags: FlagsMap; isLoaded: boolean } {
  const query = useQuery({
    queryKey: ['flags'],
    queryFn: ({ signal }) => getFlags(signal),
    staleTime: 5 * 60_000,
    retry: 1,
  });
  useSseInvalidate('flags.updated', [['flags']]);
  return { flags: query.data ?? {}, isLoaded: query.isSuccess };
}

export function useFlag(name: string): boolean {
  const { flags } = useFlags();
  return Boolean(flags[name]);
}
