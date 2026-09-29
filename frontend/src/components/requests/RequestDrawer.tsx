import type { ReactNode } from 'react';
import { Sheet, SheetContent, SheetTitle } from '@/shared/ui/sheet';
import { RequestCard } from './RequestCard';

/**
 * Карточка заявки поверх канбана — Sheet 720px справа (redesign.md §5.1/§6.4):
 * доска не теряет скролл-позицию; «Открыть отдельно» ведёт на /requests/:id.
 */
export function RequestDrawer({
  requestId,
  onClose,
}: {
  requestId: string | null;
  onClose: () => void;
}): ReactNode {
  return (
    <Sheet open={requestId !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <SheetContent
        side="right"
        className="w-full gap-0 overflow-y-auto p-6 sm:max-w-[720px]"
        aria-describedby={undefined}
      >
        <SheetTitle className="sr-only">Карточка заявки</SheetTitle>
        {requestId ? (
          <RequestCard key={requestId} requestId={requestId} inDrawer onNavigateAway={onClose} />
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
