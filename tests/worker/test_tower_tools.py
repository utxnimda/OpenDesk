import unittest
from apps.features.tower_assistant.backend.capture import CaptureManager
from apps.features.tower_assistant.backend.service import register
from opendesk_worker.registry import MethodRegistry
from opendesk_worker.protocol import ProtocolError, Request


class TowerToolsTests(unittest.TestCase):
    def test_unconfigured_snapshot_does_not_select_or_start_capture(self):
        manager = CaptureManager(now=lambda: 123, auto_poll=False)
        result = manager.readonly_summary()
        self.assertFalse(result["configured"])
        self.assertEqual(result["eventCount"], 0)
        self.assertEqual(result["sampledAtMs"], 123000)
        self.assertIsNone(manager._root)
        self.assertIsNone(manager._thread)
        self.assertNotIn("gameDir", result)
        self.assertNotIn("token", result)

    def test_declared_readonly_tool_rejects_paths(self):
        registry = MethodRegistry()
        register(registry)
        description = registry.dispatch(Request("describe", "tools.describe", {})).value
        tool = description["tools"][0]
        self.assertTrue(tool["readOnly"])
        self.assertIn(tool["method"], registry.capabilities)
        with self.assertRaises(ProtocolError):
            registry.dispatch(Request("call", "tower.ai.summary", {"gameDir": "C:/private"}))
