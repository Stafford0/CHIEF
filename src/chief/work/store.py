import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from chief.work.schema import Goal, Task, WorkPriority, WorkStatus


class WorkStore:
    """SQLite-backed source of truth for co-founder commitments."""

    def __init__(self, database_path: str | Path = "data/chief.db") -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    status TEXT NOT NULL, target_date TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, goal_id TEXT REFERENCES goals(id) ON DELETE SET NULL,
                    title TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL,
                    priority TEXT NOT NULL, due_at TEXT, blocked_reason TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_tasks_attention
                    ON tasks(status, priority, due_at);
                CREATE INDEX IF NOT EXISTS ix_tasks_goal ON tasks(goal_id);
                """
            )

    def health(self) -> bool:
        """Verify database integrity and that CHIEF can commit a tiny heartbeat."""
        try:
            with self._connect() as connection:
                result = connection.execute("PRAGMA quick_check(1)").fetchone()
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chief_health (
                        component TEXT PRIMARY KEY,
                        checked_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT OR REPLACE INTO chief_health(component, checked_at) VALUES (?, ?)",
                    ("work_store", datetime.now(UTC).isoformat()),
                )
            return result is not None and result[0] == "ok"
        except sqlite3.Error:
            return False

    def save_goal(self, goal: Goal) -> Goal:
        goal.updated_at = datetime.now(UTC)
        data = goal.model_dump(mode="json")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO goals VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                description=excluded.description, status=excluded.status,
                target_date=excluded.target_date, updated_at=excluded.updated_at""",
                tuple(
                    data[key]
                    for key in (
                        "id",
                        "title",
                        "description",
                        "status",
                        "target_date",
                        "created_at",
                        "updated_at",
                    )
                ),
            )
        return goal

    def get_goal(self, goal_id: UUID) -> Goal | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM goals WHERE id = ?", (str(goal_id),)).fetchone()
        return self._goal(row) if row else None

    def list_goals(self, include_closed: bool = False) -> list[Goal]:
        query = "SELECT * FROM goals"
        parameters: tuple[object, ...] = ()
        if not include_closed:
            query += " WHERE status NOT IN (?, ?)"
            parameters = (WorkStatus.DONE.value, WorkStatus.CANCELLED.value)
        query += " ORDER BY target_date IS NULL, target_date, updated_at DESC"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._goal(row) for row in rows]

    def save_task(self, task: Task) -> Task:
        task.updated_at = datetime.now(UTC)
        if task.status == WorkStatus.DONE and task.completed_at is None:
            task.completed_at = task.updated_at
        elif task.status != WorkStatus.DONE:
            task.completed_at = None
        data = task.model_dump(mode="json")
        keys = (
            "id",
            "goal_id",
            "title",
            "description",
            "status",
            "priority",
            "due_at",
            "blocked_reason",
            "created_at",
            "updated_at",
            "completed_at",
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET goal_id=excluded.goal_id,
                title=excluded.title, description=excluded.description,
                status=excluded.status, priority=excluded.priority, due_at=excluded.due_at,
                blocked_reason=excluded.blocked_reason, updated_at=excluded.updated_at,
                completed_at=excluded.completed_at""",
                tuple(data[key] for key in keys),
            )
        return task

    def get_task(self, task_id: UUID) -> Task | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (str(task_id),)).fetchone()
        return self._task(row) if row else None

    def list_tasks(self, include_closed: bool = False, limit: int = 200) -> list[Task]:
        if not 1 <= limit <= 1000:
            raise ValueError("Task limit must be between 1 and 1000.")
        query = "SELECT * FROM tasks"
        parameters: list[object] = []
        if not include_closed:
            query += " WHERE status NOT IN (?, ?)"
            parameters.extend((WorkStatus.DONE.value, WorkStatus.CANCELLED.value))
        priority_order = "CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END"
        query += f" ORDER BY {priority_order} DESC, due_at IS NULL, due_at, updated_at DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._task(row) for row in rows]

    @staticmethod
    def _goal(row: sqlite3.Row) -> Goal:
        return Goal(
            id=row["id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            target_date=date.fromisoformat(row["target_date"]) if row["target_date"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"],
            goal_id=row["goal_id"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
            priority=WorkPriority(row["priority"]),
            due_at=row["due_at"],
            blocked_reason=row["blocked_reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )
