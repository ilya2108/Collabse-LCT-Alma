import { MutationCache, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MotionConfig } from 'motion/react';
import { useState, type ReactNode } from 'react';
import { Toaster } from '@/shared/ui/sonner';
import { notifyApiError } from '@/shared/api/feedback';
import { AuthProvider } from '@/shared/auth/AuthContext';
import '@/shared/lib/dayjs';

/**
 * Дерево провайдеров приложения: MotionConfig → react-query → аутентификация →
 * sonner-тосты. Ошибки мутаций показываются тостом глобально; ошибки запросов
 * рендерят экранные состояния (ux.md §4.1).
 */

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: 1,
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: 0,
      },
    },
    mutationCache: new MutationCache({
      // meta.silent — мутация сама показывает ошибку (optimistic rollback канбана и т.п.)
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.silent) return;
        notifyApiError(error);
      },
    }),
  });
}

export function AppProviders({ children }: { children: ReactNode }): ReactNode {
  const [queryClient] = useState(createQueryClient);
  return (
    // MotionConfig reducedMotion="user" (redesign.md §2.4): motion сам отключает
    // transform-анимации при prefers-reduced-motion, оставляя opacity.
    <MotionConfig reducedMotion="user">
      <QueryClientProvider client={queryClient}>
        <AuthProvider>{children}</AuthProvider>
      </QueryClientProvider>
      {/* Тосты нового стека (§3.11) */}
      <Toaster />
    </MotionConfig>
  );
}
