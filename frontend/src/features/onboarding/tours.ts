import {
  Bell,
  Download,
  Eye,
  FileUp,
  GraduationCap,
  KanbanSquare,
  LayoutDashboard,
  Plug,
  ShieldCheck,
  Workflow,
} from 'lucide-react';
import type { AppRole } from '@/shared/auth/roles';
import type { RoleOnboarding, TourConfig } from './types';

/**
 * Конфиги онбординга по ролям (redesign.md §7.2, §7.4, §7.5).
 * Тексты шагов — финальные, дословно из §7.4. Целевые элементы — атрибуты
 * data-tour (стабильные id); их расстановка по чужим экранам — стадия
 * Integrate (WP6 размечает только свои: talent-funnel, talent-pii,
 * notif-thresholds).
 */

const KAM_MAIN: TourConfig = {
  id: 'kam-main',
  title: 'Доска и заявки',
  section: '/board',
  steps: [
    {
      target: 'nav',
      route: '/board',
      title: 'Навигация',
      text: 'Слева — все разделы, доступные вашей роли. Начнём с главного — доски заявок.',
      placement: 'right',
    },
    {
      target: 'board-wf-switch',
      route: '/board',
      title: 'Два процесса',
      text: 'B2B — работа с вузами, B2C — с физлицами и юрлицами. Доска показывает актуальную схему.',
    },
    {
      target: 'board-card',
      route: '/board',
      title: 'Заявка',
      text: 'Тяните карточку между этапами. Подсветятся колонки, куда переход разрешён, — возвраты тоже.',
    },
    {
      target: 'board-filters',
      route: '/board',
      title: 'Фильтры и пресеты',
      text: 'Настройте фильтры под себя и сохраните как пресет — он будет ждать вас при следующем входе.',
    },
    {
      target: 'nav-import',
      route: '/board',
      title: 'Импорт из Excel',
      text: 'Загружайте XLSX и даже старый XLS: мастер сам предложит маппинг колонок и покажет ошибки построчно.',
      placement: 'right',
    },
    {
      target: 'header-bell',
      route: '/board',
      title: 'Уведомления',
      text: 'Здесь события по вашим заявкам. Привяжите Telegram — важное придёт прямо в чат.',
    },
    {
      target: 'header-search',
      route: '/board',
      title: 'Быстрый поиск',
      text: 'Cmd+K — и вы найдёте вуз, заявку или студента, не отходя от кассы.',
    },
  ],
};

const ADMIN_MAIN: TourConfig = {
  id: 'admin-main',
  title: 'Администрирование',
  section: '/admin',
  steps: [
    {
      target: 'nav',
      title: 'Навигация',
      text: 'Вам доступна вся система, включая раздел Админ.',
      placement: 'right',
    },
    {
      target: 'wf-canvas',
      route: '/workflow',
      title: 'Конструктор',
      text: 'Конструктор: этапы и переходы — это карточки и стрелки. Тащите, соединяйте, пробуйте.',
    },
    {
      target: 'wf-properties',
      route: '/workflow',
      title: 'Панель свойств',
      text: 'Панель свойств: имя, цвет, пороги зависания и роль этапа.',
      placement: 'left',
    },
    {
      target: 'wf-submit',
      route: '/workflow',
      title: 'Согласование правок',
      text: 'Опасные правки — переименование и удаление этапа — всегда идут через согласование, даже ваши.',
    },
    {
      target: 'admin-approvals',
      route: '/admin/approvals',
      title: 'Согласования',
      text: 'Здесь вы одобряете пакеты изменений: дифф, миграция заявок, публикация в один клик.',
    },
    {
      target: 'admin-integrations',
      route: '/admin/integrations',
      title: 'Интеграции',
      text: 'Монитор LMS/CMS: журнал обмена в обе стороны и тестовые события.',
    },
    {
      target: 'admin-audit',
      route: '/admin/audit',
      title: 'Журнал аудита',
      text: 'Журнал аудита: каждый вход, переход и раскрытие ПДн — с фильтрами.',
    },
  ],
};

const HEAD_MAIN: TourConfig = {
  id: 'head-main',
  title: 'Команда и процессы',
  section: '/dashboard',
  steps: [
    {
      target: 'dashboard-filters',
      route: '/dashboard',
      title: 'Аналитика команды',
      text: 'Видите всю команду; клик по графику ведёт в отфильтрованную доску.',
    },
    {
      target: 'board-card',
      route: '/board',
      title: 'Доска заявок',
      text: 'Вам доступен drag любых заявок и смена ответственных.',
    },
    {
      target: 'wf-canvas',
      route: '/workflow',
      title: 'Черновик схемы',
      text: 'Правьте схему в черновике — на работу команды это не влияет.',
    },
    {
      target: 'wf-submit',
      route: '/workflow',
      title: 'Отправка на согласование',
      text: 'Пакет уйдёт админу; статус видно здесь.',
    },
    {
      target: 'talent-funnel',
      route: '/talent-pool',
      title: 'Пул талантов',
      text: 'Воронка студентов: от кандидата до пула талантов.',
    },
    {
      target: 'notif-thresholds',
      route: '/settings/notifications',
      title: 'Пороги эскалации',
      text: 'Настройте, когда заявка считается зависшей и когда эскалировать.',
    },
  ],
};

const OBSERVER_MAIN: TourConfig = {
  id: 'observer-main',
  title: 'Режим просмотра',
  section: '/dashboard',
  steps: [
    {
      target: 'header-observer-badge',
      title: 'Режим просмотра',
      text: 'Ваша роль — наблюдатель: все данные доступны для просмотра без изменений.',
    },
    {
      target: 'dashboard-export',
      route: '/dashboard',
      title: 'Экспорт',
      text: 'Экспортируйте PNG и XLSX — просмотр не ограничивает выгрузку.',
    },
    {
      target: 'nav-registry',
      title: 'Реестры',
      text: 'Вузы, договоры, продукты и программы — с поиском, фильтрами и пресетами.',
      placement: 'right',
    },
    {
      target: 'talent-pii',
      route: '/talent-pool',
      title: 'Маскирование ПДн',
      text: 'Персональные данные студентов всегда маскированы для вашей роли.',
    },
  ],
};

export const ROLE_ONBOARDING: Record<AppRole, RoleOnboarding> = {
  kam: {
    role: 'kam',
    welcome: {
      paragraph:
        'Здесь вы ведёте заявки своих вузов по этапам, импортируете данные из Excel и следите, чтобы ничего не зависло.',
      highlights: [
        { icon: KanbanSquare, label: 'Доска заявок' },
        { icon: FileUp, label: 'Импорт из Excel' },
        { icon: Bell, label: 'Уведомления в Telegram' },
      ],
      mainTourId: 'kam-main',
    },
    tours: [KAM_MAIN],
    checklist: [
      { id: 'tour', title: 'Пройти тур', startsTour: true },
      { id: 'open-request', title: 'Открыть карточку заявки', route: '/board' },
      { id: 'move-request', title: 'Перевести заявку по этапу', route: '/board' },
      { id: 'save-preset', title: 'Сохранить пресет таблицы', route: '/registry/universities' },
      { id: 'link-telegram', title: 'Привязать Telegram', route: '/settings/notifications' },
      { id: 'run-import', title: 'Выполнить импорт', route: '/import' },
    ],
  },
  head_kam: {
    role: 'head_kam',
    welcome: {
      paragraph:
        'Вам видны заявки всей команды: аналитика, настройка процессов и приоритеты программ.',
      highlights: [
        { icon: LayoutDashboard, label: 'Аналитика команды' },
        { icon: Workflow, label: 'Правки процессов' },
        { icon: GraduationCap, label: 'Пул талантов' },
      ],
      mainTourId: 'head-main',
    },
    tours: [HEAD_MAIN, KAM_MAIN],
    checklist: [
      { id: 'tour', title: 'Пройти тур', startsTour: true },
      { id: 'dashboard-filter', title: 'Аналитика с фильтром по КАМу', route: '/dashboard' },
      { id: 'wf-draft', title: 'Черновик процесса', route: '/workflow' },
      { id: 'wf-submit', title: 'Отправить на согласование', route: '/workflow' },
      { id: 'thresholds', title: 'Пороги эскалации', route: '/settings/notifications' },
    ],
  },
  admin: {
    role: 'admin',
    welcome: {
      paragraph:
        'Вы управляете системой: схемы процессов, согласования, пользователи и мониторинг интеграций.',
      highlights: [
        { icon: Workflow, label: 'Конструктор' },
        { icon: ShieldCheck, label: 'Согласования' },
        { icon: Plug, label: 'Интеграции' },
      ],
      mainTourId: 'admin-main',
    },
    tours: [ADMIN_MAIN, KAM_MAIN],
    checklist: [
      { id: 'tour', title: 'Пройти тур', startsTour: true },
      { id: 'open-approvals', title: 'Открыть согласования', route: '/admin/approvals' },
      { id: 'toggle-flag', title: 'Переключить фичефлаг', route: '/admin/feature-flags' },
      { id: 'test-integration', title: 'Тестовое событие интеграции', route: '/admin/integrations' },
      { id: 'open-audit', title: 'Открыть аудит', route: '/admin/audit' },
    ],
  },
  observer: {
    role: 'observer',
    welcome: {
      paragraph:
        'Вам доступен просмотр всех данных без изменений; ПДн студентов маскированы.',
      highlights: [
        { icon: Eye, label: 'Режим просмотра' },
        { icon: LayoutDashboard, label: 'Аналитика' },
        { icon: Download, label: 'Экспорт' },
      ],
      mainTourId: 'observer-main',
    },
    tours: [OBSERVER_MAIN],
    checklist: [
      { id: 'tour', title: 'Пройти тур', startsTour: true },
      { id: 'export-widget', title: 'Экспорт виджета', route: '/dashboard' },
      { id: 'open-talent-pool', title: 'Открыть пул талантов', route: '/talent-pool' },
    ],
  },
};

/** Все туры роли; в меню перезапуска текущий раздел показывается первым. */
export function toursForRole(role: AppRole): TourConfig[] {
  return ROLE_ONBOARDING[role].tours;
}
