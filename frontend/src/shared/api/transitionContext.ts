// Контекст перехода по статусу (api-contract.md §4.3): что видел пользователь в карточке.
// Сервер ничего по нему не проверяет — только объясняет 409: заявку уже перевели, закрыли,
// перенесла система, сменился ответственный (причина в details[0].code, текст — в message).
// Модуль без импортов приложения: его гоняет node --test (tests/transition-context.test.ts).

export interface TransitionExpectations {
  expected_status_id: string;
  expected_assignee_id?: string;
}

interface SeenRequest {
  status: { id: string };
  assignee: { id: string } | null;
}

/** Этап и ответственный из карточки — в тело `POST /requests/{id}/transitions`. */
export function transitionExpectations(request: SeenRequest): TransitionExpectations {
  return request.assignee
    ? { expected_status_id: request.status.id, expected_assignee_id: request.assignee.id }
    : { expected_status_id: request.status.id };
}

/**
 * Переход на самом деле уже выполнен: повтор с тем же ключом идемпотентности, а сохранённый
 * ответ недоступен (409 `idempotency_replay_unavailable`). Карточку не откатываем и пишем
 * «уже выполнен», а не «не выполнен».
 */
export function isTransitionAlreadyDone(error: unknown): boolean {
  if (typeof error !== 'object' || error === null) return false;
  const { status, code } = error as { status?: unknown; code?: unknown };
  return status === 409 && code === 'idempotency_replay_unavailable';
}
