from datetime import UTC, datetime

from chief.work.schema import BriefingItem, ExecutiveBriefing, Task, WorkPriority, WorkStatus
from chief.work.store import WorkStore

_PRIORITY = {
    WorkPriority.LOW: 10,
    WorkPriority.MEDIUM: 25,
    WorkPriority.HIGH: 45,
    WorkPriority.CRITICAL: 70,
}


def _attention(task: Task, now: datetime) -> BriefingItem:
    urgency = _PRIORITY[task.priority]
    reasons = [f"{task.priority.value} priority"]
    if task.status == WorkStatus.BLOCKED:
        urgency += 20
        reasons.append(f"blocked{': ' + task.blocked_reason if task.blocked_reason else ''}")
    if task.due_at:
        due = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)
        hours = (due - now).total_seconds() / 3600
        if hours < 0:
            urgency += 30
            reasons.append("overdue")
        elif hours <= 24:
            urgency += 20
            reasons.append("due within 24 hours")
        elif hours <= 72:
            urgency += 10
            reasons.append("due within 3 days")
    return BriefingItem(
        kind="task",
        title=task.title,
        reason="; ".join(reasons),
        urgency=min(100, urgency),
        task_id=task.id,
        goal_id=task.goal_id,
    )


def build_briefing(store: WorkStore, limit: int = 10) -> ExecutiveBriefing:
    now = datetime.now(UTC)
    tasks = store.list_tasks(limit=1000)
    items = sorted(
        (_attention(task, now) for task in tasks), key=lambda item: item.urgency, reverse=True
    )[:limit]
    counts = {
        "open_tasks": len(tasks),
        "blocked_tasks": sum(task.status == WorkStatus.BLOCKED for task in tasks),
        "overdue_tasks": sum(
            bool(
                task.due_at
                and (task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)) < now
            )
            for task in tasks
        ),
        "active_goals": len(store.list_goals()),
    }
    if not items:
        summary = "No open commitments need attention."
    else:
        summary = f"{len(items)} prioritized item{'s' if len(items) != 1 else ''}; {counts['blocked_tasks']} blocked and {counts['overdue_tasks']} overdue."
    return ExecutiveBriefing(summary=summary, items=items, counts=counts)
