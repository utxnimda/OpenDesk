from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from apps.features.virtual_partner.backend.outing_schedule import FIRST_DELAY_MS, INTERVAL_MS, tick_schedule
from apps.features.virtual_partner.backend.store import PartnerStore


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.store = PartnerStore(self.root)
        self.addCleanup(lambda: self.store.close())
        self.service = SimpleNamespace(store=self.store, lock=RLock(), closed=False, outing=None, active=None,
                                       outing_start=Mock(return_value={"id": "outing-id"}))
        self.start = int(datetime(2026, 11, 3, 10).timestamp() * 1000)
        clock = patch("apps.features.virtual_partner.backend.outing_schedule._now", return_value=self.start)
        self.now = clock.start()
        self.addCleanup(clock.stop)

    def tick(self):
        return tick_schedule(self.service, {})

    def test_disabled_busy_and_reopening_do_not_dispatch_or_replay_a_tick(self):
        self.assertIsNone(self.tick()["startedId"])
        enabled = self.store.outing_schedule.configure(True)
        self.assertEqual(enabled["nextAt"], self.start + FIRST_DELAY_MS)
        self.assertIsNone(self.tick()["startedId"])
        self.now.return_value = enabled["nextAt"]
        self.service.active = SimpleNamespace(status="running")
        self.assertIsNone(self.tick()["startedId"])
        self.service.active = None
        self.assertEqual(self.tick()["startedId"], "outing-id")
        self.store.close()
        self.service.store = self.store = PartnerStore(self.root)
        self.assertIsNone(self.tick()["startedId"])
        self.assertEqual(self.tick()["schedule"]["attemptsToday"], 1)
        self.service.outing_start.assert_called_once()
        self.store.outing_schedule.configure(False)
        self.now.return_value += 3 * INTERVAL_MS
        self.assertIsNone(self.tick()["startedId"])

    def test_daily_limit_survives_toggling_and_resets_on_the_next_local_day(self):
        due = self.store.outing_schedule.configure(True)["nextAt"]
        for index in range(3):
            self.now.return_value = due + INTERVAL_MS * index
            self.assertEqual(self.tick()["startedId"], "outing-id")
        self.store.outing_schedule.configure(False)
        self.store.outing_schedule.configure(True)
        self.now.return_value += INTERVAL_MS
        blocked = self.tick()
        self.assertIsNone(blocked["startedId"])
        midnight = int(datetime(2026, 11, 4).timestamp() * 1000)
        self.assertEqual(blocked["schedule"]["nextAt"], midnight)
        self.now.return_value = midnight
        self.assertEqual(self.tick()["schedule"]["attemptsToday"], 1)
