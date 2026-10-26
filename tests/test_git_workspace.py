import asyncio
import base64
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_orch.backup import backup_bundle, restore_bundle
from agent_orch.brain import MockBrain, fixture_plan
from agent_orch.git_workspace import git, make_git_fixture, metadata, source_snapshot
from agent_orch.integrator import integrate
from agent_orch.models import OrchError, digest, validate_result
from agent_orch.scheduler import Scheduler
from agent_orch.storage import Store
from agent_orch.verifier_registry import register_profile
from agent_orch.workspace import apply_files, copy_snapshot, manifest, change_details


class GitWorkspaces(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="helm-git-中文 空格-")
        self.root = Path(self.temp.name)
        self.store = Store(self.root/"state")
        self.source = make_git_fixture(self.store.root/"fixtures"/"synthetic")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def submit(self, plan=None):
        plan = plan or fixture_plan()
        scope = self.store.approve(plan, self.source)
        return self.store.submit(plan, self.source, scope, "request")

    def complete(self, result, allowed, profile="fixture-stats"):
        plan = fixture_plan(provider="manual")
        plan["tasks"][0].update(allowed_paths=allowed, verifier_profile=profile)
        if profile != "fixture-stats":
            plan["goal"] = plan["tasks"][0]["goal"] = "Update synthetic message"
            plan["tasks"][0]["acceptance"] = ["message.txt contains fixed"]
        run = self.submit(plan)
        response = self.root/"response.json"
        response.write_text(json.dumps(result), encoding="utf-8")
        scheduler = Scheduler(self.store, brain="interactive", manual_result=response)
        asyncio.run(scheduler.execute(run))
        task = self.store.tasks(run)[0]
        if task["state"] == "reviewing":
            ev = json.loads(self.store.db.execute("SELECT evidence FROM attempts").fetchone()[0])
            review = asyncio.run(MockBrain().review(ev, self.source))
            scheduler.interactive_review(run, task["id"], review)
        return run

    def test_dirty_index_untracked_and_ignored_are_preserved(self):
        (self.source/"README.txt").write_text("staged content\n")
        git(self.source, "add", "--", "README.txt")
        (self.source/"README.txt").write_text("unstaged content\n")
        (self.source/"local note 中文.txt").write_text("untracked synthetic note")
        (self.source/".gitignore").write_text("ignored/\n")
        (self.source/"ignored").mkdir()
        (self.source/"ignored"/".env").write_text("SYNTHETIC_ONLY=not-a-secret")
        before = source_snapshot(self.source)
        index = (self.source/".git"/"index").read_bytes()
        run = self.submit()
        self.assertEqual(asyncio.run(Scheduler(self.store).execute(run))["state"], "accepted")
        target = Path(asyncio.run(integrate(self.store, run))["workspace"])
        self.assertFalse((target/".git").exists())
        self.assertFalse((target/"ignored").exists())
        self.assertEqual((target/"local note 中文.txt").read_text(), "untracked synthetic note")
        self.assertEqual((target/"README.txt").read_text(), "unstaged content\n")
        self.assertEqual(source_snapshot(self.source), before)
        self.assertEqual((self.source/".git"/"index").read_bytes(), index)
        self.assertEqual(self.store.submission(run)["source_snapshot"]["head_commit"], before["head_commit"])
        self.assertTrue(any(s["status"] == "MM" for s in before["status"]))

    def test_head_change_after_approval_requires_reapproval(self):
        plan = fixture_plan()
        with patch("agent_orch.git_workspace.source_snapshot", side_effect=AssertionError("unauthorized source must not be inspected")):
            with self.assertRaises(OrchError):
                self.store.submit(plan, self.source, "../../not-an-approval", "bad-scope")
        scope = self.store.approve(plan, self.source)
        git(self.source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "test: baseline drift")
        with self.assertRaises(OrchError):
            self.store.submit(plan, self.source, scope, "request")

    def test_index_change_with_same_working_tree_requires_reapproval(self):
        (self.source/"README.txt").write_text("already dirty")
        plan = fixture_plan()
        scope = self.store.approve(plan, self.source)
        before = manifest(self.source)
        git(self.source, "add", "--", "README.txt")
        self.assertEqual(manifest(self.source), before)
        with self.assertRaises(OrchError):
            self.store.submit(plan, self.source, scope, "request")

    def test_git_index_drift_after_submit_is_rejected(self):
        (self.source/"README.txt").write_text("dirty")
        run = self.submit()
        git(self.source, "add", "--", "README.txt")
        self.assertEqual(asyncio.run(Scheduler(self.store).execute(run))["state"], "failed")

    def test_integration_detects_git_head_drift_without_tree_change(self):
        run = self.submit()
        asyncio.run(Scheduler(self.store).execute(run))
        git(self.source, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-m", "test: later commit")
        with self.assertRaises(OrchError):
            asyncio.run(integrate(self.store, run))

    def test_preexisting_tracked_deletion_is_not_recreated(self):
        (self.source/"README.txt").unlink()
        before = metadata(self.source)
        run = self.submit()
        self.assertEqual(asyncio.run(Scheduler(self.store).execute(run))["state"], "accepted")
        target = Path(asyncio.run(integrate(self.store, run))["workspace"])
        self.assertFalse((target/"README.txt").exists())
        self.assertEqual(metadata(self.source), before)

    def test_git_symlink_mode_and_submodule_are_rejected(self):
        oid = git(self.source, "rev-parse", "HEAD:stats.py").stdout.decode().strip()
        git(self.source, "update-index", "--add", "--cacheinfo", "120000,"+oid+",fake-link")
        with self.assertRaises(OrchError):
            source_snapshot(self.source)
        git(self.source, "update-index", "--force-remove", "fake-link")
        head = git(self.source, "rev-parse", "HEAD").stdout.decode().strip()
        git(self.source, "update-index", "--add", "--cacheinfo", "160000,"+head+",fake-module")
        with self.assertRaises(OrchError):
            source_snapshot(self.source)

    def test_external_filters_and_config_includes_fail_closed(self):
        git(self.source, "config", "filter.fixture.clean", "not-an-executable")
        with self.assertRaises(OrchError):
            source_snapshot(self.source)
        git(self.source, "config", "--unset", "filter.fixture.clean")
        git(self.source, "config", "include.path", str(self.root/"do-not-read-config"))
        with self.assertRaises(OrchError):
            source_snapshot(self.source)

    def test_version_two_add_delete_rename_and_binary_integration(self):
        (self.source/"obsolete.txt").write_text("old")
        (self.source/"rename me.txt").write_text("retain bytes")
        before = source_snapshot(self.source)
        payload = b"\x00\xff\x01synthetic"
        result = {"schema_version": 2, "summary": "synthetic operations", "operations": [
            {"action": "write", "path": "stats.py", "content": "def total(values):\n    return sum(values)\n"},
            {"action": "delete", "path": "obsolete.txt"},
            {"action": "rename", "path": "rename me.txt", "target": "docs/renamed 中文.txt"},
            {"action": "write", "path": "assets/blob.bin", "encoding": "base64", "content": base64.b64encode(payload).decode()},
            {"action": "write", "path": "new.txt", "content": "new synthetic file"}]}
        allowed = ["stats.py", "obsolete.txt", "rename me.txt", "docs/renamed 中文.txt", "assets/blob.bin", "new.txt"]
        run = self.complete(result, allowed)
        self.assertEqual(self.store.run(run)["state"], "accepted")
        integration = asyncio.run(integrate(self.store, run))
        target = Path(integration["workspace"])
        self.assertFalse((target/"obsolete.txt").exists())
        self.assertFalse((target/"rename me.txt").exists())
        self.assertEqual((target/"docs"/"renamed 中文.txt").read_text(), "retain bytes")
        self.assertEqual((target/"assets"/"blob.bin").read_bytes(), payload)
        self.assertTrue(any(c["action"] == "rename" for c in integration["changes"]))
        self.assertTrue(any(c.get("binary") for c in integration["changes"]))
        self.assertEqual(source_snapshot(self.source), before)

    def test_operation_validation_precedes_file_writes(self):
        before = manifest(self.source)
        result = {"schema_version": 2, "summary": "invalid", "operations": [
            {"action": "write", "path": "stats.py", "content": "must not be written"},
            {"action": "write", "path": "new.bin", "encoding": "base64", "content": "!!!"}]}
        with self.assertRaises(OrchError):
            apply_files(self.source, result, ["stats.py", "new.bin"])
        self.assertEqual(manifest(self.source), before)

    def test_rename_scope_case_collision_and_metadata_escape_rejected(self):
        for operations, allowed in [
            ([{"action": "rename", "path": "README.txt", "target": "outside.txt"}], ["README.txt"]),
            ([{"action": "delete", "path": ".git/config"}], [".git/config"]),
            ([{"action": "write", "path": "A.txt", "content": "a"}, {"action": "write", "path": "a.txt", "content": "a"}], ["A.txt", "a.txt"]),
            ([{"action": "rename", "path": "README.txt", "target": "../outside"}], ["README.txt", "../outside"])]:
            with self.assertRaises(OrchError):
                validate_result({"schema_version": 2, "summary": "invalid", "operations": operations}, allowed)

    def test_rename_cannot_overwrite_existing_file(self):
        target = self.root/"isolated"
        copy_snapshot(self.source, target)
        with self.assertRaises(OrchError):
            apply_files(target, {"schema_version": 2, "summary": "invalid", "operations": [{"action": "rename", "path": "README.txt", "target": "stats.py"}]}, ["README.txt", "stats.py"])
        self.assertTrue((target/"README.txt").exists())

    def test_executable_mode_handling_matches_platform(self):
        target = self.root/"isolated"
        copy_snapshot(self.source, target)
        result = {"schema_version": 2, "summary": "mode", "operations": [{"action": "write", "path": "stats.py", "content": (target/"stats.py").read_text(), "executable": True}]}
        if os.name == "nt":
            with self.assertRaises(OrchError):
                apply_files(target, result, ["stats.py"])
        else:
            apply_files(target, result, ["stats.py"])
            self.assertTrue(manifest(target)["stats.py"]["executable"])
            self.assertNotEqual(manifest(target), manifest(self.source))

    def register_checker(self, ident="trusted-check"):
        script = self.root/(ident+".py")
        script.write_text("import pathlib,sys\np=pathlib.Path(sys.argv[1])/'message.txt'\npassed=p.read_text()=='fixed'\nprint('trusted verifier',passed)\nraise SystemExit(0 if passed else 1)\n", encoding="utf-8")
        definition = {"schema_version": 1, "argv": [sys.executable, "-I", "-B", str(script), "{workspace}"], "pinned_files": [str(script)], "timeout": 10}
        register_profile(self.store.root, ident, definition)
        return script, definition

    def test_registered_fixed_verifier_end_to_end(self):
        script, _ = self.register_checker()
        (self.source/"message.txt").write_text("broken")
        run = self.complete({"summary": "fix data", "files": [{"path": "message.txt", "content": "fixed"}]}, ["message.txt"], "trusted-check")
        self.assertEqual(self.store.run(run)["state"], "accepted")
        evidence = json.loads(self.store.db.execute("SELECT evidence FROM attempts").fetchone()[0])
        self.assertFalse(evidence["baseline"]["passed"])
        self.assertTrue(evidence["verification"]["passed"])
        integration = asyncio.run(integrate(self.store, run))
        self.assertEqual(integration["verification"]["identity"], "registered:trusted-check")
        self.assertEqual((self.source/"message.txt").read_text(), "broken")

    def test_unknown_or_modified_verifier_cannot_run(self):
        plan = fixture_plan()
        plan["tasks"][0]["verifier_profile"] = "not-registered"
        with self.assertRaises(OrchError):
            self.store.approve(plan, self.source)
        script, _ = self.register_checker()
        (self.source/"message.txt").write_text("broken")
        plan["tasks"][0]["verifier_profile"] = "trusted-check"
        run = self.submit(plan)
        script.write_text("raise SystemExit(0)")
        self.assertEqual(asyncio.run(Scheduler(self.store).execute(run))["state"], "failed")

    def test_verifier_registration_is_immutable_and_external(self):
        script, definition = self.register_checker()
        with self.assertRaises(OrchError):
            register_profile(self.store.root, "trusted-check", definition)
        inside = self.source/"worker-editable-check.py"
        inside.write_text("pass")
        definition["argv"][3] = str(inside)
        definition["pinned_files"] = [str(inside)]
        with self.assertRaises(OrchError):
            register_profile(self.store.root, "inside-state", definition)
        external_source = make_git_fixture(self.root/"external-source")
        inside = external_source/"worker-editable-check.py"
        inside.write_text("pass")
        definition["argv"][3] = str(inside)
        definition["pinned_files"] = [str(inside)]
        register_profile(self.store.root, "inside-source", definition)
        plan = fixture_plan()
        plan["tasks"][0]["verifier_profile"] = "inside-source"
        with self.assertRaises(OrchError):
            self.store.approve(plan, external_source)

    def test_backup_restore_preserves_git_baseline_and_registry(self):
        self.register_checker()
        run = self.submit()
        asyncio.run(Scheduler(self.store).execute(run))
        backup = self.root/"backup"
        backup_bundle(self.store, backup)
        restore_bundle(backup, self.root/"restored")
        restored = Store(self.root/"restored")
        try:
            self.assertTrue((restored.root/"verifiers"/"trusted-check.json").exists())
            self.assertTrue((restored.root/"fixtures"/"synthetic"/".git"/"HEAD").exists())
            integration = asyncio.run(integrate(restored, run))
            self.assertTrue(integration["verification"]["passed"])
        finally:
            restored.close()


if __name__ == "__main__":
    unittest.main()
