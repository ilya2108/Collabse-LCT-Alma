import type { ReactNode } from 'react';
import { RouterProvider } from 'react-router-dom';
import { AppProviders } from '@/app/providers/AppProviders';
import { router } from '@/app/router';

export function App(): ReactNode {
  return (
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  );
}
