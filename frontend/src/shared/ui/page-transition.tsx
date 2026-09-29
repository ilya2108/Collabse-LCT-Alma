import { AnimatePresence, motion } from 'motion/react';
import type { ReactNode } from 'react';
import { useLocation } from 'react-router-dom';
import { motionTokens } from '@/shared/lib/motion';

/**
 * Вход страницы (§2.3): обёртка вокруг <Outlet/> в AppLayout (key = pathname).
 * initial {opacity 0, y: 8} → {1, 0} duration.normal easing.smooth;
 * exit {opacity 0, y: -8} duration.fast. AnimatePresence mode="wait".
 * reduced-motion гасится MotionConfig reducedMotion="user" в корне.
 */
export function PageTransition({ children }: { children: ReactNode }): ReactNode {
  const { pathname } = useLocation();
  return (
    <AnimatePresence mode="wait">
      <motion.div
        key={pathname}
        initial={{ opacity: 0, y: motionTokens.distance.sm }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: -motionTokens.distance.sm }}
        transition={{ duration: motionTokens.duration.normal, ease: motionTokens.easing.smooth }}
        style={{ minHeight: '100%' }}
      >
        {children}
      </motion.div>
    </AnimatePresence>
  );
}
