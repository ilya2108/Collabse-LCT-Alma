import { api } from '../client';
import type { CurrentUser } from '../types';

/** Профиль, роли и permissions текущего пользователя (api-contract.md §2.1). */
export function fetchMe(): Promise<CurrentUser> {
  return api.get<CurrentUser>('/auth/me');
}
