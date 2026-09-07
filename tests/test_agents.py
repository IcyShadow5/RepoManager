import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from repo_manager import agents


class FakeProcess:
    pid = 1234
    def __init__(self, code=None):
        self.code = code
        self.terminated = False
    def poll(self):
        return self.code
    def terminate(self):
        self.terminated = True
        self.code = -15


class SlowTerminateProcess(FakeProcess):
    def terminate(self):
        self.terminated = True


class AgentTests(unittest.TestCase):
    def test_availability_states(self):
        self.assertEqual(agents.agent_availability({}, which=lambda _: None), agents.INVALID_CONFIGURATION)
        agent = agents.new_agent("a", "Test", "test-agent")
        self.assertEqual(agents.agent_availability(agent, which=lambda _: "/bin/test"), agents.AVAILABLE)
        self.assertEqual(agents.agent_availability(agent, which=lambda _: None), agents.UNAVAILABLE)

    def test_launch_intent_requires_existing_explicit_target(self):
        agent = agents.new_agent("a", "Test", sys.executable, args=["--safe"])
        target = {"kind": "repository", "path": tempfile.gettempdir()}
        intent = agents.launch_intent(agent, target)
        self.assertEqual(intent["argv"], [sys.executable, "--safe"])
        self.assertEqual(intent["target"]["kind"], "repository")
        with self.assertRaises(ValueError):
            agents.launch_intent(agent, {"path": "missing-target"})

    def test_run_lifecycle_and_post_run_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            intent = {"agent_id": "a", "target": {"kind": "worktree", "path": tmp},
                      "cwd": tmp, "argv": [sys.executable],
                      "launch_time": agents.utc_now()}
            run = agents.new_run(intent)
            process = FakeProcess()
            self.assertIs(agents.start_run(run, popen=lambda *a, **k: process), process)
            self.assertEqual(run["process_state"], agents.RUNNING)
            self.assertEqual(agents.observe_run(run, process), agents.RUNNING)
            process.code = 0
            self.assertEqual(agents.observe_run(run, process), agents.EXITED)
            agents.verify_post_run(run, observe=lambda _: {"branch": "main", "head": "abc", "dirty": 1, "worktrees": []})
            self.assertEqual(run["verification"], agents.VERIFIED)
            self.assertEqual(run["post_run"]["dirty"], 1)

    def test_nonzero_exit_and_failed_start_are_distinct(self):
        run = agents.new_run({"agent_id": "a", "target": {}, "cwd": "missing",
                              "argv": [sys.executable], "launch_time": agents.utc_now()})
        self.assertIsNone(agents.start_run(run, popen=lambda *a, **k: FakeProcess()))
        self.assertEqual(run["process_state"], agents.FAILED_TO_START)
        with tempfile.TemporaryDirectory() as tmp:
            run["cwd"] = tmp
            process = FakeProcess(code=2)
            agents.start_run(run, popen=lambda *a, **k: process)
            self.assertEqual(agents.observe_run(run, process), agents.EXITED)
            self.assertEqual(run["exit_code"], 2)

    def test_duplicate_running_run_is_blocked_by_pure_check(self):
        runs = [{"agent_id": "a", "cwd": "/repo", "process_state": agents.RUNNING}]
        self.assertTrue(agents.has_running_run(runs, "a", "/repo"))
        self.assertFalse(agents.has_running_run(runs, "a", "/other"))

    def test_cancellation_does_not_claim_rollback(self):
        run = agents.new_run({"agent_id": "a", "target": {}, "cwd": tempfile.gettempdir(),
                              "argv": [sys.executable], "launch_time": agents.utc_now()})
        process = FakeProcess()
        agents.start_run(run, popen=lambda *a, **k: process)
        self.assertEqual(agents.cancel_run(run, process), agents.TERMINATED)
        self.assertTrue(process.terminated)

    def test_stop_remains_stopping_until_exit_is_observed(self):
        run = agents.new_run({
            "agent_id": "a", "target": {}, "cwd": tempfile.gettempdir(),
            "argv": [sys.executable], "launch_time": agents.utc_now(),
        })
        process = SlowTerminateProcess()
        agents.start_run(run, popen=lambda *a, **k: process)
        self.assertEqual(agents.cancel_run(run, process), agents.STOPPING)
        self.assertEqual(run["process_state"], agents.STOPPING)
        process.code = 0
        self.assertEqual(agents.observe_run(run, process), agents.TERMINATED)

    def test_preflight_rejects_changed_target_configuration_and_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            executable = Path(tmp) / "agent.exe"
            executable.write_bytes(b"fixture")
            target = {"kind": "repository", "path": tmp,
                      "project_id": "project-a"}
            agent = agents.new_agent("a", "Test", str(executable),
                                     args=["--safe"])
            intent = agents.launch_intent(agent, target)

            changed_target = dict(target, project_id="project-b")
            run = agents.new_run(intent)
            self.assertIsNone(agents.start_run(
                run, agent=agent, target=changed_target,
                popen=lambda *a, **k: FakeProcess()))
            self.assertIn("identity", run["failure"])

            run = agents.new_run(intent)
            changed_agent = dict(agent, args=["--changed"])
            self.assertIsNone(agents.start_run(
                run, agent=changed_agent, target=target,
                popen=lambda *a, **k: FakeProcess()))
            self.assertIn("configuration changed", run["failure"])

            run = agents.new_run(intent)
            executable.unlink()
            self.assertIsNone(agents.start_run(
                run, agent=agent, target=target,
                popen=lambda *a, **k: FakeProcess()))
            self.assertIn("no longer available", run["failure"])

    def test_readiness_requires_executable_and_concrete_target(self):
        agent = agents.new_agent("a", "Test", sys.executable)
        self.assertEqual(
            agents.agent_readiness(agent, None)["state"], agents.NOT_READY)
        ready = agents.agent_readiness(
            agent, {"path": tempfile.gettempdir()})
        self.assertEqual(ready["state"], agents.READY)
        self.assertEqual(ready["availability"], agents.AVAILABLE)

    def test_real_bounded_process_start_stop_and_confirmed_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = {"kind": "repository", "path": tmp,
                      "project_id": "runtime-target"}
            agent = agents.new_agent(
                "runtime", "Runtime fixture", sys.executable,
                args=["-c", "import time; time.sleep(30)"])
            run = agents.new_run(agents.launch_intent(agent, target))
            process = agents.start_run(run, agent=agent, target=target)
            self.assertIsNotNone(process)
            try:
                self.assertEqual(run["process_state"], agents.RUNNING)
                state = agents.cancel_run(run, process)
                self.assertIn(state, (agents.STOPPING, agents.TERMINATED))
                deadline = time.time() + 10
                while state == agents.STOPPING and time.time() < deadline:
                    time.sleep(0.02)
                    state = agents.observe_run(run, process)
                self.assertEqual(state, agents.TERMINATED)
                self.assertIsNotNone(run["exit_code"])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)

    def test_real_bounded_process_success_is_observed_as_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = {"kind": "repository", "path": tmp,
                      "project_id": "success-target"}
            agent = agents.new_agent(
                "runtime", "Runtime fixture", sys.executable,
                args=["-c", "raise SystemExit(0)"])
            run = agents.new_run(agents.launch_intent(agent, target))
            process = agents.start_run(run, agent=agent, target=target)
            self.assertEqual(process.wait(timeout=10), 0)
            self.assertEqual(agents.observe_run(run, process), agents.EXITED)
            self.assertEqual(run["exit_code"], 0)

    def test_missing_post_run_target_is_not_verified(self):
        run = agents.new_run({"agent_id": "a", "target": {}, "cwd": tempfile.gettempdir(),
                              "argv": ["stub"], "launch_time": agents.utc_now()})
        agents.verify_post_run(run, observe=lambda _: None)
        self.assertEqual(run["verification"], agents.FAILED)


if __name__ == "__main__":
    unittest.main()
