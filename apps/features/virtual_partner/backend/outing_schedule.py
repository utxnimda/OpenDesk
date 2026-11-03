"""Opt-in, page-driven outings. SQLite owns the clock and the attempt budget."""
from datetime import datetime, time, timedelta

from .records import StoreError, _now, _uid


INTERVAL_MS = 60 * 60_000
FIRST_DELAY_MS = 60_000
DAILY_LIMIT = 3


class OutingSchedule:
    def __init__(self, db, lock):
        self.db = db
        self.lock = lock

    def initialize(self):
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='partner_outing_schedule'").fetchone():
            self.view()
            return
        self.db.execute("""CREATE TABLE partner_outing_schedule (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL,
            enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), due_at INTEGER NOT NULL,
            day TEXT NOT NULL, attempts INTEGER NOT NULL CHECK(attempts BETWEEN 0 AND 3))""")
        self.db.execute("""INSERT INTO partner_outing_schedule
            (singleton,version,enabled,due_at,day,attempts) VALUES(1,1,0,0,'',0)""")

    def _view(self, now):
        row = self.db.execute("SELECT * FROM partner_outing_schedule WHERE singleton=1").fetchone()
        if not row or row["version"] != 1:
            raise StoreError("DATA_SCHEMA", "自动外出设置版本不兼容，原记录已保留。")
        today = datetime.fromtimestamp(now / 1000).date()
        count = row["attempts"] if row["day"] == today.isoformat() else 0
        due = row["due_at"]
        if count >= DAILY_LIMIT:
            midnight = datetime.combine(today + timedelta(days=1), time.min)
            due = max(due, int(midnight.timestamp() * 1000))
        return {"enabled": bool(row["enabled"]), "nextAt": due if row["enabled"] else None,
                "attemptsToday": count, "dailyLimit": DAILY_LIMIT, "intervalMinutes": INTERVAL_MS // 60_000}

    def view(self):
        with self.lock:
            return self._view(_now())

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise StoreError("INVALID_PARAMS", "请选择是否启用自动外出。")
        with self.lock, self.db:
            now = _now()
            if enabled != self._view(now)["enabled"]:
                self.db.execute("""UPDATE partner_outing_schedule SET enabled=?,
                    due_at=CASE WHEN ? THEN max(due_at,?) ELSE due_at END WHERE singleton=1""",
                    (enabled, enabled, now + FIRST_DELAY_MS))
            return self._view(now)

    def reserve(self):
        # Charge before dispatch. Lost replies and worker exits must not send another request.
        with self.lock, self.db:
            now = _now()
            current = self._view(now)
            if not current["enabled"] or now < current["nextAt"]:
                return None
            day = datetime.fromtimestamp(now / 1000).date().isoformat()
            self.db.execute("""UPDATE partner_outing_schedule SET due_at=?,day=?,attempts=?
                WHERE singleton=1""", (now + INTERVAL_MS, day, current["attemptsToday"] + 1))
            return "auto." + _uid()


def configure_schedule(service, params):
    with service.lock:
        return service.store.outing_schedule.configure(params["enabled"])


def tick_schedule(service, _params):
    with service.lock:
        schedule = service.store.outing_schedule
        result = {"schedule": schedule.view(), "startedId": None}
        if (service.closed or service.outing is not None or
                service.active and service.active.status in ("running", "cancelling")):
            return result
        request_id = schedule.reserve()
        if request_id:
            result["startedId"] = service.outing_start({"requestId": request_id})["id"]
            result["schedule"] = schedule.view()
        return result
