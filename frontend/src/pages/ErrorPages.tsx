import type { ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { Illustration, type IllustrationName } from '@/shared/illustrations';
import { Button } from '@/shared/ui/button';

/** Полноэкранные 403/404 с иллюстрацией §4.2 и кнопкой «На дашборд» (ux.md §4.1). */

function ErrorScreen({
  illustration,
  title,
  description,
}: {
  illustration: IllustrationName;
  title: string;
  description: string;
}): ReactNode {
  const navigate = useNavigate();
  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center gap-4 px-6 py-16 text-center">
      <Illustration name={illustration} height={200} />
      <h1 className="text-xl font-semibold text-balance">{title}</h1>
      <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      <Button onClick={() => navigate('/dashboard')}>К аналитике</Button>
    </div>
  );
}

export function ForbiddenPage(): ReactNode {
  return (
    <ErrorScreen
      illustration="error-403"
      title="Нет доступа"
      description="Этот раздел недоступен для вашей роли. Если вы считаете это ошибкой — обратитесь к администратору."
    />
  );
}

export function NotFoundPage(): ReactNode {
  return (
    <ErrorScreen
      illustration="error-404"
      title="Страница не найдена"
      description="Такой страницы нет или она была перемещена."
    />
  );
}
