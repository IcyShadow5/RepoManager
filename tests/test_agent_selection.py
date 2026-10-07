import sys
import unittest
from repo_manager import agents


class AgentCatalogTests(unittest.TestCase):
    def test_zero_one_multiple_detected_commands(self):
        settings = {"agent_cmd": "missing"}
        for count in range(len(agents.DETECTABLE_COMMANDS) + 1):
            commands = dict(agents.DETECTABLE_COMMANDS[:count])
            which = lambda cmd: "/agents/" + cmd if cmd in commands else None
            items = agents.agent_catalog(settings, which=which)
            self.assertEqual(sum(i["availability"] == agents.AVAILABLE for i in items), count)

    def test_duplicate_command_is_not_a_second_choice(self):
        items = agents.agent_catalog({"agent_cmd": "opencode"}, which=lambda cmd: "/agents/opencode" if cmd == "opencode" else None)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["agent_id"], "configured")

    def test_custom_target_missing_and_invalid_settings_remain_explicit(self):
        items = agents.agent_catalog({"agent_cmd": "missing", "agent_targets": [{"name": "bad"}]}, which=lambda _: None)
        self.assertEqual(items[0]["availability"], agents.UNAVAILABLE)
        self.assertEqual(items[1]["availability"], agents.INVALID_CONFIGURATION)
        self.assertIn("configuration_error", items[1])

    def test_configuration_accepts_named_executable_and_rejects_duplicate_identity(self):
        target = agents.new_agent("custom:stable", "My Agent", sys.executable)
        self.assertEqual(agents.validate_agent_targets([target]), [target])
        with self.assertRaises(ValueError):
            agents.validate_agent_targets([target, target])
        with self.assertRaises(ValueError):
            agents.validate_agent_targets([{**target, "args": "--bad"}])

    def test_resolving_targets_does_not_execute_them(self):
        calls = []
        agents.agent_catalog({"agent_cmd": "missing"}, which=lambda cmd: calls.append(cmd))
        self.assertTrue(calls)

    def test_freebuff_is_preferred_and_opencode_remains_available(self):
        def which(cmd):
            return "/agents/" + cmd + ".cmd" if cmd in ("freebuff", "opencode") else None
        items = agents.agent_catalog({"agent_cmd": "opencode"}, which=which)
        self.assertEqual([item["display_name"] for item in items], ["Freebuff", "OpenCode"])
        self.assertEqual(items[0]["agent_id"], "detected:freebuff")
        self.assertTrue(all(item["availability"] == agents.AVAILABLE for item in items))

    def test_duplicate_resolution_preserves_saved_agent_identity(self):
        def which(cmd):
            return "/agents/freebuff.cmd" if cmd in ("freebuff", "alias") else None
        custom = agents.new_agent("custom:saved", "Saved Freebuff", "alias")
        items = agents.agent_catalog({"agent_cmd": "freebuff", "agent_targets": [custom],
                                      "selected_agent_id": "custom:saved"}, which=which)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["agent_id"], "custom:saved")
