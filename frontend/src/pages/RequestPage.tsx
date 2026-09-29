import type { ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import { RequestCard } from '@/components/requests/RequestCard';

/** Полная страница карточки заявки — /requests/:id (ux.md §8). */
export function RequestPage(): ReactNode {
  const { id = '' } = useParams<'id'>();
  return <RequestCard key={id} requestId={id} />;
}
