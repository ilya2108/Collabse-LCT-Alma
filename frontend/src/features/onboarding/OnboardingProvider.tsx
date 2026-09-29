import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { createPreset, listPresets, updatePreset } from '@/shared/api/endpoints/presets';
import type { UiPresetState } from '@/shared/api/types';
import { useAuth } from '@/shared/auth/AuthContext';
import type { AppRole } from '@/shared/auth/roles';
import { ROLE_ONBOARDING } from './tours';
import {
  DEFAULT_ONBOARDING_STATE,
  ONBOARDING_VERSION,
  type ChecklistItemConfig,
  type OnboardingState,
  type RoleOnboarding,
  type TourConfig,
} from './types';
import { SpotlightTour } from './SpotlightTour';
import { WelcomeDialog } from './WelcomeDialog';

/**
 * Провайдер онбординга (redesign.md §7.1): состояние per-user в /ui/presets
 * (screen: "onboarding", name: "state"), загрузка после auth, запись —
 * PATCH с debounce 1с (optimistic). Роль определяет набор туров/чек-листа
 * (берётся старшая: admin > head_kam > kam > observer). Монтируется внутри
 * Router (стадия Integrate); до монтирования useOnboarding отдаёт no-op.
 */

const SCREEN = 'onboarding';
const PRESET_NAME = 'state';
const SAVE_DEBOUNCE_MS = 1000;

export interface OnboardingContextValue {
  /** Состояние загружено (после auth). */
  ready: boolean;
  role: AppRole | null;
  state: OnboardingState;
  /** Конфиг роли: welcome, туры, чек-лист. */
  config: RoleOnboarding | null;
  tours: TourConfig[];
  activeTourId: string | null;
  startTour: (tourId: string) => void;
  /** Идемпотентная отметка пункта чек-листа (§7.5) — зовут хуки экранов. */
  completeChecklistItem: (id: string) => void;
  checklistItems: ChecklistItemConfig[];
  hideChecklist: () => void;
  showChecklist: () => void;
  /** Приветственный экран заново (§7.6). */
  showWelcome: () => void;
}

const NOOP_CONTEXT: OnboardingContextValue = {
  ready: false,
  role: null,
  state: DEFAULT_ONBOARDING_STATE,
  config: null,
  tours: [],
  activeTourId: null,
  startTour: () => undefined,
  completeChecklistItem: () => undefined,
  checklistItems: [],
  hideChecklist: () => undefined,
  showChecklist: () => undefined,
  showWelcome: () => undefined,
};

const OnboardingContext = createContext<OnboardingContextValue | null>(null);

/**
 * Публичный хук фичи. Безопасен без провайдера (до стадии Integrate
 * возвращает no-op) — экраны WP6 могут звать completeChecklistItem всегда.
 */
export function useOnboarding(): OnboardingContextValue {
  return useContext(OnboardingContext) ?? NOOP_CONTEXT;
}

export function OnboardingProvider({ children }: { children: ReactNode }): ReactNode {
  const { phase, user, mainRole } = useAuth();
  const [state, setState] = useState<OnboardingState>(DEFAULT_ONBOARDING_STATE);
  const [loaded, setLoaded] = useState(false);
  const [welcomeOpen, setWelcomeOpen] = useState(false);
  const [activeTourId, setActiveTourId] = useState<string | null>(null);

  const presetIdRef = useRef<string | null>(null);
  const saveTimerRef = useRef<number | null>(null);
  const pendingRef = useRef<OnboardingState | null>(null);
  const creatingRef = useRef(false);
  const pendingChecklistRef = useRef<Set<string>>(new Set());

  const config = mainRole ? ROLE_ONBOARDING[mainRole] : null;

  // --- Загрузка состояния после auth (§7.1) ---------------------------------
  useEffect(() => {
    if (phase !== 'ready' || !user) return;
    let cancelled = false;
    listPresets(SCREEN)
      .then((res) => {
        if (cancelled) return;
        const preset = res.items.find((p) => p.name === PRESET_NAME);
        if (preset) {
          presetIdRef.current = preset.id;
          const raw = preset.state as unknown as Partial<OnboardingState>;
          setState({
            ...DEFAULT_ONBOARDING_STATE,
            ...raw,
            tours: raw.tours ?? {},
            checklist: raw.checklist ?? {},
            version: typeof raw.version === 'number' ? raw.version : 0,
          });
        }
        setLoaded(true);
      })
      .catch(() => {
        // Онбординг не должен ломать приложение: работаем в памяти сессии.
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [phase, user]);

  // --- Запись: PATCH с debounce 1с, optimistic (§7.1) ------------------------
  const persist = useCallback((next: OnboardingState) => {
    pendingRef.current = next;
    if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    saveTimerRef.current = window.setTimeout(() => {
      saveTimerRef.current = null;
      const payload = pendingRef.current;
      if (!payload) return;
      const presetState = payload as unknown as UiPresetState;
      if (presetIdRef.current) {
        updatePreset(presetIdRef.current, { state: presetState }).catch(() => undefined);
      } else if (!creatingRef.current) {
        creatingRef.current = true;
        createPreset({ screen: SCREEN, name: PRESET_NAME, is_default: false, state: presetState })
          .then((created) => {
            presetIdRef.current = created.id;
          })
          .catch(() => undefined)
          .finally(() => {
            creatingRef.current = false;
          });
      }
    }, SAVE_DEBOUNCE_MS);
  }, []);

  useEffect(
    () => () => {
      if (saveTimerRef.current !== null) window.clearTimeout(saveTimerRef.current);
    },
    [],
  );

  const patchState = useCallback(
    (patch: (current: OnboardingState) => OnboardingState) => {
      setState((current) => {
        const next = patch(current);
        if (next === current) return current;
        persist(next);
        return next;
      });
    },
    [persist],
  );

  // --- Автозапуск welcome при первом входе / росте версии (§7.1, §7.2) -------
  useEffect(() => {
    if (!loaded || !config) return;
    if (!state.welcome_seen || state.version < ONBOARDING_VERSION) {
      setWelcomeOpen(true);
    }
    // только по факту загрузки — дальше welcome управляется вручную
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded, config]);

  const markWelcomeSeen = useCallback(() => {
    patchState((current) =>
      current.welcome_seen && current.version >= ONBOARDING_VERSION
        ? current
        : { ...current, welcome_seen: true, version: ONBOARDING_VERSION },
    );
  }, [patchState]);

  const completeChecklistItem = useCallback(
    (id: string) => {
      if (!loaded || !config) {
        pendingChecklistRef.current.add(id);
        return;
      }
      if (!config.checklist.some((item) => item.id === id)) return;
      patchState((current) =>
        current.checklist[id] ? current : { ...current, checklist: { ...current.checklist, [id]: true } },
      );
    },
    [loaded, config, patchState],
  );

  // Отметки, пришедшие до загрузки состояния, применяются после неё.
  useEffect(() => {
    if (!loaded || !config) return;
    for (const id of pendingChecklistRef.current) completeChecklistItem(id);
    pendingChecklistRef.current.clear();
  }, [loaded, config, completeChecklistItem]);

  const startTour = useCallback(
    (tourId: string) => {
      setWelcomeOpen(false);
      markWelcomeSeen();
      setActiveTourId(tourId);
    },
    [markWelcomeSeen],
  );

  const finishTour = useCallback(
    (tourId: string, status: 'done' | 'skipped') => {
      setActiveTourId(null);
      patchState((current) => ({
        ...current,
        tours: { ...current.tours, [tourId]: status },
        checklist:
          status === 'done' && config?.checklist.some((i) => i.id === 'tour')
            ? { ...current.checklist, tour: true }
            : current.checklist,
      }));
    },
    [patchState, config],
  );

  const hideChecklist = useCallback(() => {
    patchState((current) => ({ ...current, checklist_hidden: true }));
  }, [patchState]);

  const showChecklist = useCallback(() => {
    patchState((current) => ({ ...current, checklist_hidden: false }));
  }, [patchState]);

  const showWelcome = useCallback(() => setWelcomeOpen(true), []);

  const activeTour = useMemo(
    () => config?.tours.find((tour) => tour.id === activeTourId) ?? null,
    [config, activeTourId],
  );

  const value = useMemo<OnboardingContextValue>(
    () => ({
      ready: loaded,
      role: mainRole,
      state,
      config,
      tours: config?.tours ?? [],
      activeTourId,
      startTour,
      completeChecklistItem,
      checklistItems: config?.checklist ?? [],
      hideChecklist,
      showChecklist,
      showWelcome,
    }),
    [
      loaded,
      mainRole,
      state,
      config,
      activeTourId,
      startTour,
      completeChecklistItem,
      hideChecklist,
      showChecklist,
      showWelcome,
    ],
  );

  return (
    <OnboardingContext.Provider value={value}>
      {children}
      {config ? (
        <WelcomeDialog
          open={welcomeOpen && loaded}
          userName={user?.full_name ?? user?.username ?? ''}
          config={config}
          onStartTour={() => startTour(config.welcome.mainTourId)}
          onClose={() => {
            setWelcomeOpen(false);
            markWelcomeSeen();
          }}
        />
      ) : null}
      {activeTour ? (
        <SpotlightTour
          key={activeTour.id}
          tour={activeTour}
          onFinish={() => finishTour(activeTour.id, 'done')}
          onSkip={() => finishTour(activeTour.id, 'skipped')}
        />
      ) : null}
    </OnboardingContext.Provider>
  );
}
