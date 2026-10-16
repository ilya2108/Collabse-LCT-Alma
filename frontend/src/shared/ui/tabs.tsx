import * as TabsPrimitive from '@radix-ui/react-tabs';
import * as React from 'react';
import { cn } from '@/shared/lib/cn';

/**
 * Tabs (§5.1): подчёркнутый вариант — линия под списком, активный таб
 * border-b-2 border-primary text-primary.
 * 375 px (P1-5): список не шире контейнера и прокручивается вбок сам — страница
 * по горизонтали не едет. Линия под списком — inset-тень, а не border + -mb-px:
 * при overflow-x-auto выступ на 1px давал вертикальную прокрутку и обрезал подчёркивание.
 */
const Tabs = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Root>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Root
    ref={ref}
    data-slot="tabs"
    className={cn('flex flex-col gap-4', className)}
    {...props}
  />
));
Tabs.displayName = 'Tabs';

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    data-slot="tabs-list"
    className={cn(
      'inline-flex w-fit max-w-full items-center gap-4 overflow-x-auto overflow-y-hidden shadow-[inset_0_-1px_0_var(--color-border)] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden',
      className,
    )}
    {...props}
  />
));
TabsList.displayName = 'TabsList';

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    data-slot="tabs-trigger"
    className={cn(
      'inline-flex shrink-0 items-center justify-center gap-1.5 whitespace-nowrap border-b-2 border-transparent px-1 pb-2 pt-1 text-sm font-medium text-muted-foreground transition-[color,border-color] duration-150',
      'hover:text-foreground',
      'data-[state=active]:border-primary data-[state=active]:text-primary',
      'disabled:pointer-events-none disabled:opacity-50',
      // кольцо фокуса внутрь: снаружи его обрезал бы overflow списка
      'outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring rounded-t-sm',
      '[&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*=size-])]:size-4',
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = 'TabsTrigger';

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    data-slot="tabs-content"
    className={cn('flex-1 outline-none', className)}
    {...props}
  />
));
TabsContent.displayName = 'TabsContent';

export { Tabs, TabsList, TabsTrigger, TabsContent };
