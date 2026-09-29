import { LogIn } from 'lucide-react';
import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { LoginHeroIllustration } from '@/app/localIllustrations';
import { AuthErrorScreen, AuthSplash } from '@/app/router/guards';
import { useAuth } from '@/shared/auth/AuthContext';
import { staggerContainer, staggerItem } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';

/**
 * Экран входа (redesign.md §6.1): сплит-лейаут — слева панель bg-sidebar
 * с иллюстрацией `login-hero` (скрывается на планшете), справа карточка 360px
 * с кнопкой входа через Keycloak (OIDC + PKCE). Вход элементов — stagger.
 * После логина возвращаемся на исходный deep-link (ux.md §5).
 */
export function LoginPage(): ReactNode {
  const { phase, login, retry } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? '/dashboard';

  if (phase === 'initializing') return <AuthSplash />;
  if (phase === 'ready') return <Navigate to={from} replace />;
  if (phase === 'error') {
    return <AuthErrorScreen title="Не удалось подключиться к Keycloak" onRetry={retry} />;
  }

  return (
    <div className="flex min-h-screen bg-background">
      <motion.section
        variants={staggerContainer}
        initial="hidden"
        animate="visible"
        className="hidden w-[44%] max-w-[560px] flex-col items-start justify-center gap-6 bg-sidebar px-12 lg:flex"
      >
        <motion.div variants={staggerItem}>
          <LoginHeroIllustration height={220} />
        </motion.div>
        <motion.h2 variants={staggerItem} className="text-2xl font-semibold text-white">
          Альма
        </motion.h2>
        <motion.p variants={staggerItem} className="text-sm text-sidebar-muted">
          Партнёрства с вузами: заявки, процессы, пул талантов
        </motion.p>
      </motion.section>

      <div className="flex min-w-0 flex-1 items-center justify-center px-4">
        <motion.div
          variants={staggerContainer}
          initial="hidden"
          animate="visible"
          className="flex w-full max-w-[360px] flex-col items-center gap-4 rounded-xl border border-border bg-card p-8 text-center shadow-card"
        >
          <motion.img
            variants={staggerItem}
            src="/favicon.svg"
            alt=""
            width={56}
            height={56}
          />
          <motion.h1 variants={staggerItem} className="text-xl font-semibold">
            Вход
          </motion.h1>
          <motion.div variants={staggerItem} className="w-full">
            <Button size="lg" className="w-full" onClick={() => login(from)}>
              <LogIn aria-hidden="true" />
              Войти через Keycloak
            </Button>
          </motion.div>
          <motion.p variants={staggerItem} className="text-xs text-muted-foreground">
            CRM взаимодействия с вузами · единый вход организации
          </motion.p>
        </motion.div>
      </div>
    </div>
  );
}
