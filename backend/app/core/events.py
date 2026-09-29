"""Реестры событий системы (data-model.md §9.4, api-contract.md §2.2, §9.4).

Единственное место, где перечислены строковые коды событий: outbox нотификаций,
конверт IntegrationEvent и SSE-топики UI. Модули импортируют константы отсюда —
опечатка в коде события ловится на импорте, а не в проде.
"""

from __future__ import annotations

# --- события нотификаций (notification_rule.event_type, data-model.md §9.4) ------------

EVENT_REQUEST_CREATED = "request.created"
EVENT_REQUEST_TRANSITIONED = "request.transitioned"
EVENT_REQUEST_ASSIGNED = "request.assigned"
EVENT_REQUEST_STUCK = "request.stuck"
EVENT_REQUEST_COMMENT_ADDED = "request.comment_added"
EVENT_WORKFLOW_CHANGE_REQUESTED = "workflow.change_requested"
EVENT_WORKFLOW_CHANGED = "workflow.changed"
EVENT_STUDENT_TALENT_POOL_ADDED = "student.talent_pool_added"
EVENT_IMPORT_FINISHED = "import.finished"
EVENT_INTEGRATION_LEAD_RECEIVED = "integration.lead_received"
EVENT_INTEGRATION_DELIVERY_FAILED = "integration.delivery_failed"

# служебное событие тестовой отправки (§9.1); в notification_rule не используется —
# CHECK DDL его не включает, но таблица `notification` ограничения на event_type не имеет
EVENT_NOTIFICATION_TEST = "notification.test"

NOTIFICATION_EVENT_TYPES = frozenset(
    {
        EVENT_REQUEST_CREATED,
        EVENT_REQUEST_TRANSITIONED,
        EVENT_REQUEST_ASSIGNED,
        EVENT_REQUEST_STUCK,
        EVENT_REQUEST_COMMENT_ADDED,
        EVENT_WORKFLOW_CHANGE_REQUESTED,
        EVENT_WORKFLOW_CHANGED,
        EVENT_STUDENT_TALENT_POOL_ADDED,
        EVENT_IMPORT_FINISHED,
        EVENT_INTEGRATION_LEAD_RECEIVED,
        EVENT_INTEGRATION_DELIVERY_FAILED,
    }
)

# шаблоны notification-service (api-contract.md §9.4); неизвестные события —
# фолбэк `event.type` → `event_type` (сервис рендерит generic-текст из body)
NOTIFICATION_TEMPLATES: dict[str, str] = {
    EVENT_REQUEST_STUCK: "request_stuck",
    EVENT_REQUEST_TRANSITIONED: "request_transitioned",
    EVENT_REQUEST_CREATED: "request_assigned",
    EVENT_REQUEST_ASSIGNED: "request_assigned",
    EVENT_REQUEST_COMMENT_ADDED: "comment_added",
    EVENT_WORKFLOW_CHANGE_REQUESTED: "workflow_change_requested",
    EVENT_WORKFLOW_CHANGED: "workflow_changed",
    EVENT_STUDENT_TALENT_POOL_ADDED: "talent_pool_added",
    EVENT_INTEGRATION_LEAD_RECEIVED: "lead_received",
    EVENT_NOTIFICATION_TEST: "test_message",
}


def notification_template(event_type: str) -> str:
    return NOTIFICATION_TEMPLATES.get(event_type, event_type.replace(".", "_"))


# --- интеграционные события (конверт IntegrationEvent, api-contract.md §13) --------------

INTG_CRM_REQUEST_HANDED_OVER = "crm.request.handed_over"
INTG_CRM_PROGRAM_UPSERTED = "crm.program.upserted"
INTG_CRM_PROGRAM_PUBLISHED = "crm.program.published"
INTG_CRM_PROGRAM_UNPUBLISHED = "crm.program.unpublished"
INTG_LMS_LEARNING_STARTED = "lms.learning.started"
INTG_LMS_LEARNING_PROGRESS = "lms.learning.progress"
INTG_LMS_LEARNING_COMPLETED = "lms.learning.completed"
INTG_CMS_LEAD_CREATED = "cms.lead.created"

LMS_INBOUND_EVENTS = frozenset(
    {INTG_LMS_LEARNING_STARTED, INTG_LMS_LEARNING_PROGRESS, INTG_LMS_LEARNING_COMPLETED}
)
CMS_INBOUND_EVENTS = frozenset({INTG_CMS_LEAD_CREATED})

# --- SSE-топики UI (api-contract.md §2.2) ------------------------------------------------

SSE_WORKFLOW_CHANGED = "workflow.changed"
SSE_REQUEST_TRANSITIONED = "request.transitioned"
SSE_REQUEST_COMMENT_ADDED = "request.comment_added"
SSE_TELEGRAM_LINKED = "telegram.linked"
SSE_FLAGS_UPDATED = "flags.updated"
SSE_NOTIFICATION_CREATED = "notification.created"
