// Черновики текстовых полей в sessionStorage (ФТ13 «действия пользователя кэшируются»):
// переживают F5 и переход между экранами, но не закрытие вкладки — в комментарии могут
// быть ПДн, поэтому не localStorage. Любая ошибка хранилища (приватный режим, квота,
// запрет) — черновик просто не сохраняется, экран работает как раньше.
import { useCallback, useState } from 'react';

type DraftStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;

function sessionStore(): DraftStorage | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function readDraft(key: string, storage: DraftStorage | null = sessionStore()): string {
  try {
    return storage?.getItem(key) ?? '';
  } catch {
    return '';
  }
}

/** Пустой черновик удаляет ключ — после отправки в хранилище ничего не остаётся. */
export function writeDraft(
  key: string,
  value: string,
  storage: DraftStorage | null = sessionStore(),
): void {
  try {
    if (value) storage?.setItem(key, value);
    else storage?.removeItem(key);
  } catch {
    // квота или запрет хранилища — без черновика
  }
}

/** `useState` для черновика: значение по ключу, смена ключа подхватывает свой черновик. */
export function useSessionDraft(key: string): [string, (value: string) => void] {
  const [state, setState] = useState(() => ({ key, value: readDraft(key) }));
  let current = state.value;
  if (state.key !== key) {
    // другой объект (тот же компонент, новый id) — его черновик, а не текст прежнего
    current = readDraft(key);
    setState({ key, value: current });
  }
  const setValue = useCallback(
    (value: string) => {
      setState({ key, value });
      writeDraft(key, value);
    },
    [key],
  );
  return [current, setValue];
}
