import unittest

from repo_manager import launchers


def candidate(label, priority, healthy=True, reason=""):
    return {"label": label, "type": "test", "priority": priority,
            "healthy": healthy, "reason": reason}


class UnavailableLauncherVisibilityTests(unittest.TestCase):
    def test_mixed_availability_keeps_unavailable_candidate_in_hidden_view(self):
        available = candidate("A", 10)
        unavailable = candidate("B", 20, False, "tool missing")
        primary, visible, hidden = launchers.bounded_launcher_view([available, unavailable], limit=0)
        self.assertIs(primary, available)
        self.assertEqual([item["label"] for item in visible + hidden], ["B"])
        self.assertEqual(hidden[0]["reason"], "tool missing")

    def test_ambiguous_healthy_candidates_and_unavailable_candidate_are_all_retained(self):
        commands = [candidate("dev:web", 10), candidate("dev:api", 10),
                    candidate("dev:desktop", 10, False, "desktop tool missing")]
        primary, visible, hidden = launchers.bounded_launcher_view(commands, limit=1)
        self.assertIsNone(primary)
        self.assertEqual(
            {item["label"] for item in visible + hidden},
            {"dev:web", "dev:api", "dev:desktop"},
        )
        self.assertEqual(
            next(item for item in visible + hidden if item["label"] == "dev:desktop")["reason"],
            "desktop tool missing",
        )

    def test_all_unavailable_candidates_have_complete_reasons_and_no_primary(self):
        commands = [candidate("npm", 10, False, "npm missing"),
                    candidate("Godot", 11, False, "Godot missing"),
                    candidate("Custom", 12, False, "executable missing")]
        primary, visible, hidden = launchers.bounded_launcher_view(commands, limit=1)
        self.assertIsNone(primary)
        self.assertEqual(len(visible) + len(hidden), 3)
        self.assertEqual([item["label"] for item in visible + hidden], ["npm", "Godot", "Custom"])
        self.assertTrue(all(item["reason"] for item in visible + hidden))

    def test_scaling_preserves_every_available_and_unavailable_candidate(self):
        for size in (1, 5, 20, 100, 200):
            with self.subTest(size=size):
                commands = [candidate(f"launcher-{i}", i, i % 3 != 0,
                                     "missing" if i % 3 == 0 else "") for i in range(size)]
                primary, visible, hidden = launchers.bounded_launcher_view(commands, limit=4)
                retained = visible + hidden + ([primary] if primary else [])
                self.assertEqual(len(retained), size)
                self.assertEqual(len({item["label"] for item in retained}), size)
                self.assertEqual(
                    {item["label"] for item in retained},
                    {f"launcher-{i}" for i in range(size)},
                )


if __name__ == "__main__":
    unittest.main()
