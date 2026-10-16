import { Info } from 'lucide-react';
import type { ReactNode } from 'react';
import type { SlaDefinition } from '@/shared/api/endpoints/sla';
import { Button } from '@/shared/ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '@/shared/ui/popover';

/**
 * «Как считается» вкладки «Просрочки» и блок «Оговорки о данных». Определения —
 * из ответа API (`definitions`, тот же текст, что в XLSX и PDF); пока backend их не
 * отдаёт — локальная копия ниже, сверенная со спецификацией (§2.5). Только факты и
 * правила подсчёта, без оценок.
 */

export const SLA_DEFINITIONS: SlaDefinition[] = [
  {
    term: 'Просрочено',
    text: 'Заявка на этапе дольше нормы этапа (строго больше, с точностью до секунды). То же, что «зависшие» на других вкладках, фильтр доски «Зависшие» и команда бота.',
  },
  {
    term: 'Норма этапа',
    text: 'Порог зависания из настроек процесса: задан для этапа, иначе общий для процесса. Календарные дни, включая выходные и праздники.',
  },
  {
    term: 'Бейдж и напоминание',
    text: 'Бейдж на карточке заявки и первое напоминание считают целые сутки, поэтому появляются до суток позже, чем заявка попадает в отчёт.',
  },
  {
    term: 'Действующая норма',
    text: 'Все этапы, в том числе прошлых периодов, оцениваются по норме, которая действует сейчас. Если норму меняли, это указано в оговорках.',
  },
  {
    term: 'Этап',
    text: 'Этап на дату среза. Перевод схемы процесса и события LMS время на этапе не обнуляют.',
  },
  {
    term: 'Чья заявка',
    text: 'Заявка учитывается за тем, кто вёл её на дату среза (по истории передач), а в «Детализации» — за текущим ответственным. «Получена просроченной» — КАМ получил заявку, когда срок уже прошёл; такие заявки в правило отбора не входят.',
  },
  {
    term: 'Только свои заявки',
    text: 'КАМ видит заявки, где он ответственный сейчас; этапы, пройденные до передачи ему заявки, в его отчёт не попадают.',
  },
  {
    term: 'Уложились в норму',
    text: 'Из этапов, по которым в периоде наступило решение: заявка ушла с этапа до срока (уложились) или срок истёк (не уложились). Этапы, которые ещё не дошли до срока, не учитываются.',
  },
  {
    term: 'Сейчас сверх нормы',
    text: 'Задержка просроченных заявок на дату среза. Медиана («обычно») — у половины просрочек задержка меньше.',
  },
  {
    term: 'Опоздание закрытых этапов',
    text: 'Насколько дольше нормы длился этап, с которого заявку перевели в периоде.',
  },
  {
    term: 'Без действий после срока',
    text: 'После истечения нормы в заявке не было ручного перехода, комментария или файла — любого автора.',
  },
  {
    term: 'Собственное действие',
    text: 'В блоке напоминаний — действие получателя напоминания; в «Активности КАМов» — текущего ответственного, поэтому после передач числа расходятся.',
  },
  {
    term: 'Архив',
    text: 'Архивированная заявка исключается из всех периодов, в том числе прошлых.',
  },
  {
    term: 'Округление',
    text: 'Задержка показывается до 0,1 дня, корзина считается по целым суткам вверх: задержка чуть больше 3 суток видна как «+3 дн.», но попадает в корзину «4–7 дн.».',
  },
];

export function SlaHowItWorks({ definitions }: { definitions?: SlaDefinition[] | null }): ReactNode {
  const items = definitions && definitions.length > 0 ? definitions : SLA_DEFINITIONS;
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-muted-foreground">
          <Info aria-hidden="true" />
          Как считается
        </Button>
      </PopoverTrigger>
      <PopoverContent align="start" className="max-h-[70vh] w-[min(92vw,460px)] overflow-y-auto">
        <dl className="flex flex-col gap-2 text-sm">
          {items.map((item) => (
            <div key={item.term}>
              <dt className="font-medium">{item.term}</dt>
              <dd className="text-muted-foreground">{item.text}</dd>
            </div>
          ))}
        </dl>
      </PopoverContent>
    </Popover>
  );
}

/** Факты о данных (смена норм, переводы этапов, передачи, журнал напоминаний). */
export function SlaNotes({ notes }: { notes: string[] }): ReactNode {
  if (notes.length === 0) return null;
  return (
    <section
      aria-label="Оговорки о данных"
      className="rounded-lg border border-dashed border-border bg-card px-4 py-3"
    >
      <h2 className="mb-1.5 text-sm font-semibold">Оговорки о данных</h2>
      <ul className="flex flex-col gap-1 text-sm text-muted-foreground">
        {notes.map((note) => (
          <li key={note} className="flex gap-2">
            <span className="mt-2 size-1 shrink-0 rounded-full bg-muted-foreground" aria-hidden="true" />
            <span className="min-w-0">{note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
