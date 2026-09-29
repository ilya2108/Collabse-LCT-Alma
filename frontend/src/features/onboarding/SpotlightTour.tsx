import { X } from 'lucide-react';
import { motion, useReducedMotion } from 'motion/react';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router-dom';
import { cn } from '@/shared/lib/cn';
import { springs } from '@/shared/lib/motion';
import { Button } from '@/shared/ui/button';
import type { TourConfig } from './types';

/**
 * Spotlight-тур — своя реализация (redesign.md §7.3, Р10): portal, оверлей
 * с «дырой» через box-shadow, layout-анимация рамки между шагами
 * (springs.gentle), карточка шага 320px с автофлипом и clamp в вьюпорт.
 * Если шаг на другом маршруте — тур сам делает navigate и ждёт появления
 * data-tour-цели (raf-поллинг, таймаут 3с → шаг пропускается).
 * A11y: role="dialog" aria-modal, focus-trap, Esc = пропустить, ←/→ —
 * навигация, фон inert, при reduced-motion рамка без анимации.
 */

interface SpotlightTourProps {
  tour: TourConfig;
  onFinish: () => void;
  onSkip: () => void;
}

interface HoleRect {
  top: number;
  left: number;
  width: number;
  height: number;
}

const HOLE_PADDING = 8;
const CARD_WIDTH = 320;
const CARD_GAP = 12;
const VIEWPORT_MARGIN = 16;
const WAIT_TIMEOUT_MS = 3000;
/** Затемнение всего, кроме выреза (§7.3 — значение из спеки). */
const DIM_SHADOW = '0 0 0 9999px oklch(0.267 0.069 258.9 / 0.55)';

type Placement = 'bottom' | 'top' | 'right' | 'left';

function computeCardPosition(
  rect: HoleRect,
  cardHeight: number,
  preferred: Placement,
): { top: number; left: number } {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const order: Placement[] = [preferred, 'bottom', 'top', 'right', 'left'];

  const fits = (p: Placement): boolean => {
    switch (p) {
      case 'bottom':
        return rect.top + rect.height + CARD_GAP + cardHeight <= vh - VIEWPORT_MARGIN;
      case 'top':
        return rect.top - CARD_GAP - cardHeight >= VIEWPORT_MARGIN;
      case 'right':
        return rect.left + rect.width + CARD_GAP + CARD_WIDTH <= vw - VIEWPORT_MARGIN;
      case 'left':
        return rect.left - CARD_GAP - CARD_WIDTH >= VIEWPORT_MARGIN;
    }
  };

  const placement = order.find(fits) ?? preferred;
  let top: number;
  let left: number;
  switch (placement) {
    case 'bottom':
      top = rect.top + rect.height + CARD_GAP;
      left = rect.left + rect.width / 2 - CARD_WIDTH / 2;
      break;
    case 'top':
      top = rect.top - CARD_GAP - cardHeight;
      left = rect.left + rect.width / 2 - CARD_WIDTH / 2;
      break;
    case 'right':
      top = rect.top + rect.height / 2 - cardHeight / 2;
      left = rect.left + rect.width + CARD_GAP;
      break;
    case 'left':
      top = rect.top + rect.height / 2 - cardHeight / 2;
      left = rect.left - CARD_GAP - CARD_WIDTH;
      break;
  }
  // clamp в вьюпорт
  left = Math.min(Math.max(left, VIEWPORT_MARGIN), vw - CARD_WIDTH - VIEWPORT_MARGIN);
  top = Math.min(Math.max(top, VIEWPORT_MARGIN), Math.max(vh - cardHeight - VIEWPORT_MARGIN, VIEWPORT_MARGIN));
  return { top, left };
}

export function SpotlightTour({ tour, onFinish, onSkip }: SpotlightTourProps): ReactNode {
  const navigate = useNavigate();
  const location = useLocation();
  const reduced = useReducedMotion() ?? false;

  const [stepIndex, setStepIndex] = useState(0);
  const [rect, setRect] = useState<HoleRect | null>(null);
  const [cardHeight, setCardHeight] = useState(180);

  const cardRef = useRef<HTMLDivElement | null>(null);
  const scrolledRef = useRef<string | null>(null);

  const step = tour.steps[stepIndex]!;
  const isLast = stepIndex === tour.steps.length - 1;

  const goNext = useCallback(() => {
    if (isLast) onFinish();
    else setStepIndex((i) => i + 1);
  }, [isLast, onFinish]);

  const goPrev = useCallback(() => {
    setStepIndex((i) => Math.max(0, i - 1));
  }, []);

  // --- Навигация к маршруту шага (§7.3) --------------------------------------
  useEffect(() => {
    if (step.route && location.pathname !== step.route) {
      navigate(step.route);
    }
    // location.pathname меняется самим navigate — не перезапускаем эффект
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, navigate]);

  // --- Поиск цели + трекинг геометрии (raf, таймаут 3с → пропуск шага) --------
  useEffect(() => {
    let raf = 0;
    let disposed = false;
    const startedAt = performance.now();
    setRect(null);

    const tick = (): void => {
      if (disposed) return;
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
      if (!el) {
        if (performance.now() - startedAt > WAIT_TIMEOUT_MS) {
          // цель не появилась — шаг пропускается (§7.3)
          goNext();
          return;
        }
        raf = requestAnimationFrame(tick);
        return;
      }
      const key = `${tour.id}:${stepIndex}`;
      if (scrolledRef.current !== key) {
        scrolledRef.current = key;
        el.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
      }
      const box = el.getBoundingClientRect();
      const next: HoleRect = {
        top: Math.round(box.top - HOLE_PADDING),
        left: Math.round(box.left - HOLE_PADDING),
        width: Math.round(box.width + HOLE_PADDING * 2),
        height: Math.round(box.height + HOLE_PADDING * 2),
      };
      setRect((prev) =>
        prev &&
        prev.top === next.top &&
        prev.left === next.left &&
        prev.width === next.width &&
        prev.height === next.height
          ? prev
          : next,
      );
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
    };
  }, [step, stepIndex, tour.id, goNext, reduced]);

  // --- Фон inert + фокус на карточке ------------------------------------------
  useEffect(() => {
    const root = document.getElementById('root');
    root?.setAttribute('inert', '');
    return () => root?.removeAttribute('inert');
  }, []);

  useEffect(() => {
    cardRef.current?.focus({ preventScroll: true });
  }, [stepIndex, rect === null]);

  useLayoutEffect(() => {
    if (cardRef.current) setCardHeight(cardRef.current.offsetHeight);
  }, [stepIndex, rect]);

  // --- Клавиатура: Esc = пропустить, ←/→ = навигация, Tab — trap (§7.3) --------
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onSkip();
        return;
      }
      if (e.key === 'ArrowRight') {
        e.preventDefault();
        goNext();
        return;
      }
      if (e.key === 'ArrowLeft') {
        e.preventDefault();
        goPrev();
        return;
      }
      if (e.key === 'Tab' && cardRef.current) {
        const focusables = cardRef.current.querySelectorAll<HTMLElement>(
          'button, [href], [tabindex]:not([tabindex="-1"])',
        );
        if (focusables.length === 0) return;
        const first = focusables[0]!;
        const last = focusables[focusables.length - 1]!;
        const active = document.activeElement;
        if (e.shiftKey && (active === first || active === cardRef.current)) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    return () => document.removeEventListener('keydown', onKeyDown, true);
  }, [onSkip, goNext, goPrev]);

  const cardPos = rect
    ? computeCardPosition(rect, cardHeight, step.placement ?? 'bottom')
    : { top: window.innerHeight / 2 - 90, left: window.innerWidth / 2 - CARD_WIDTH / 2 };

  return createPortal(
    <div className="fixed inset-0 z-50">
      {rect ? (
        <motion.div
          layout
          layoutDependency={stepIndex}
          transition={reduced ? { duration: 0 } : springs.gentle}
          className="fixed rounded-xl ring-2 ring-primary"
          style={{
            top: rect.top,
            left: rect.left,
            width: rect.width,
            height: rect.height,
            borderRadius: 12,
            boxShadow: DIM_SHADOW,
          }}
          aria-hidden="true"
        />
      ) : (
        <div
          className="fixed inset-0"
          style={{ background: 'oklch(0.267 0.069 258.9 / 0.55)' }}
          aria-hidden="true"
        />
      )}
      <motion.div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${step.title} — шаг ${stepIndex + 1} из ${tour.steps.length}`}
        tabIndex={-1}
        initial={reduced ? { opacity: 0 } : { opacity: 0, y: 8, scale: 0.98 }}
        animate={reduced ? { opacity: 1 } : { opacity: 1, y: 0, scale: 1 }}
        transition={springs.gentle}
        key={`${tour.id}-${stepIndex}`}
        className="fixed rounded-lg border bg-card p-4 shadow-overlay outline-none"
        style={{ top: cardPos.top, left: cardPos.left, width: CARD_WIDTH }}
      >
        <div className="flex items-start justify-between gap-2">
          <span className="text-xs text-muted-foreground tabular">
            Шаг {stepIndex + 1} из {tour.steps.length}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="-mr-2 -mt-2 size-7 text-muted-foreground"
            aria-label="Пропустить тур"
            onClick={onSkip}
          >
            <X aria-hidden="true" />
          </Button>
        </div>
        <h2 className="mt-1 text-base font-semibold text-balance">{step.title}</h2>
        <p className="mt-1 text-sm text-muted-foreground">{step.text}</p>
        <div className="mt-4 flex items-center justify-between gap-2">
          <div className="flex items-center gap-1.5" aria-hidden="true">
            {tour.steps.map((s, index) => (
              <span
                key={s.target + String(index)}
                className={cn(
                  'size-1.5 rounded-full transition-colors duration-150',
                  index === stepIndex ? 'bg-primary' : 'bg-border',
                )}
              />
            ))}
          </div>
          <div className="flex items-center gap-2">
            {stepIndex > 0 ? (
              <Button variant="ghost" size="sm" onClick={goPrev}>
                Назад
              </Button>
            ) : null}
            <Button size="sm" onClick={goNext}>
              {isLast ? 'Готово' : 'Далее'}
            </Button>
          </div>
        </div>
      </motion.div>
    </div>,
    document.body,
  );
}
