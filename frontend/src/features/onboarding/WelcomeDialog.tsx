import { motion } from 'motion/react';
import type { ReactNode } from 'react';
import { Illustration } from '@/shared/illustrations';
import { springs } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/shared/ui/dialog';
import type { RoleOnboarding } from './types';

/**
 * Welcome-экран первого входа (redesign.md §7.2): Dialog 560px, иллюстрация,
 * абзац по роли, 3 плашки «что вы можете» со stagger springs.bouncy,
 * кнопки «Начать тур (≈1 мин)» / «Позже». Показывается один раз
 * (welcome_seen), любое закрытие ставит true.
 */

interface WelcomeDialogProps {
  open: boolean;
  userName: string;
  config: RoleOnboarding;
  onStartTour: () => void;
  onClose: () => void;
}

const containerVariants = {
  hidden: {},
  visible: { transition: { staggerChildren: 0.08, delayChildren: 0.15 } },
} as const;

const itemVariants = {
  hidden: { opacity: 0, y: 12, scale: 0.96 },
  visible: { opacity: 1, y: 0, scale: 1, transition: springs.bouncy },
} as const;

export function WelcomeDialog({
  open,
  userName,
  config,
  onStartTour,
  onClose,
}: WelcomeDialogProps): ReactNode {
  const firstName = userName.split(' ')[0] || userName;
  return (
    <Dialog open={open} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogContent className="sm:max-w-[560px]">
        <div className="flex justify-center pt-2">
          <Illustration name="onboarding-welcome" height={200} />
        </div>
        <DialogHeader className="text-center sm:text-center">
          <DialogTitle className="text-balance text-xl">
            Добро пожаловать в Альму{firstName ? `, ${firstName}` : ''}!
          </DialogTitle>
          <DialogDescription className="text-sm">{config.welcome.paragraph}</DialogDescription>
        </DialogHeader>
        <motion.ul
          className="grid grid-cols-1 gap-2 sm:grid-cols-3"
          variants={containerVariants}
          initial="hidden"
          animate={open ? 'visible' : 'hidden'}
        >
          {config.welcome.highlights.map(({ icon: Icon, label }) => (
            <motion.li
              key={label}
              variants={itemVariants}
              className="flex items-center gap-2.5 rounded-md bg-primary-tint px-3 py-2.5 sm:flex-col sm:gap-2 sm:py-4 sm:text-center"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-md bg-primary-tint-2 text-primary">
                <Icon className="size-4.5" aria-hidden="true" />
              </span>
              <span className="text-sm font-medium text-balance">{label}</span>
            </motion.li>
          ))}
        </motion.ul>
        <DialogFooter className="mt-2 gap-2 sm:justify-center">
          <Button variant="ghost" onClick={onClose}>
            Позже
          </Button>
          <Button onClick={onStartTour}>Начать тур (≈1 мин)</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
