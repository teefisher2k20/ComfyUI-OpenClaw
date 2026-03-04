import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.append(os.getcwd())

from services.planner_registry import PlannerRegistry


class TestPlannerRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.package_root = os.path.join(self.tmp, "package")
        self.state_root = os.path.join(self.tmp, "state")
        os.makedirs(self.package_root, exist_ok=True)
        os.makedirs(self.state_root, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_profiles(self, root, payload):
        with open(os.path.join(root, "profiles.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)

    def _write_prompt(self, root, text):
        with open(os.path.join(root, "system_prompt.txt"), "w", encoding="utf-8") as f:
            f.write(text)

    def test_state_override_takes_precedence(self):
        self._write_profiles(
            self.package_root,
            {
                "version": 1,
                "default_profile": "pkg",
                "profiles": [
                    {"id": "pkg", "version": "1", "label": "Package", "defaults": {}}
                ],
            },
        )
        self._write_prompt(self.package_root, "Profile {{profile_id}}")
        self._write_profiles(
            self.state_root,
            {
                "version": 1,
                "default_profile": "state",
                "profiles": [
                    {"id": "state", "version": "2", "label": "State", "defaults": {}}
                ],
            },
        )
        self._write_prompt(self.state_root, "State {{profile_label}}")

        registry = PlannerRegistry(
            package_root=self.package_root, state_root=self.state_root
        )

        self.assertEqual(registry.get_default_profile_id(), "state")
        self.assertEqual(
            [profile.id for profile in registry.list_profiles()], ["state"]
        )
        self.assertIn("State", registry.render_system_prompt("state"))
        self.assertEqual(registry.get_debug_info()["profile_source"], "state")

    def test_invalid_state_override_falls_back_to_package(self):
        self._write_profiles(
            self.package_root,
            {
                "version": 1,
                "default_profile": "pkg",
                "profiles": [
                    {"id": "pkg", "version": "1", "label": "Package", "defaults": {}}
                ],
            },
        )
        self._write_prompt(self.package_root, "Profile {{profile_id}}")
        self._write_profiles(self.state_root, {"version": 1, "default_profile": "bad"})
        self._write_prompt(self.state_root, "State {{profile_id}}")

        registry = PlannerRegistry(
            package_root=self.package_root, state_root=self.state_root
        )

        self.assertEqual(registry.get_default_profile_id(), "pkg")
        self.assertEqual([profile.id for profile in registry.list_profiles()], ["pkg"])
        self.assertEqual(registry.get_debug_info()["profile_source"], "package")
        self.assertIn("state:", registry.get_debug_info()["last_profile_error"])

    def test_invalid_prompt_template_falls_back_to_package_prompt(self):
        self._write_profiles(
            self.package_root,
            {
                "version": 1,
                "default_profile": "pkg",
                "profiles": [
                    {"id": "pkg", "version": "1", "label": "Package", "defaults": {}}
                ],
            },
        )
        self._write_prompt(self.package_root, "Package {{profile_id}}")
        self._write_profiles(
            self.state_root,
            {
                "version": 1,
                "default_profile": "pkg",
                "profiles": [
                    {"id": "pkg", "version": "1", "label": "Package", "defaults": {}}
                ],
            },
        )
        self._write_prompt(self.state_root, "Broken {{unknown_placeholder}}")

        registry = PlannerRegistry(
            package_root=self.package_root, state_root=self.state_root
        )

        self.assertEqual(registry.render_system_prompt("pkg"), "Package pkg")
        self.assertEqual(registry.get_debug_info()["prompt_source"], "package")
        self.assertIn("state:", registry.get_debug_info()["last_prompt_error"])

    def test_hot_reload_picks_up_new_state_profile(self):
        self._write_profiles(
            self.package_root,
            {
                "version": 1,
                "default_profile": "pkg",
                "profiles": [
                    {"id": "pkg", "version": "1", "label": "Package", "defaults": {}}
                ],
            },
        )
        self._write_prompt(self.package_root, "Profile {{profile_id}}")
        registry = PlannerRegistry(
            package_root=self.package_root, state_root=self.state_root
        )
        self.assertEqual(registry.get_default_profile_id(), "pkg")

        time.sleep(1.1)
        self._write_profiles(
            self.state_root,
            {
                "version": 1,
                "default_profile": "hot",
                "profiles": [
                    {"id": "hot", "version": "1", "label": "Hot", "defaults": {}}
                ],
            },
        )
        os.utime(os.path.join(self.state_root, "profiles.json"), None)

        self.assertEqual(registry.get_default_profile_id(), "hot")
        self.assertEqual([profile.id for profile in registry.list_profiles()], ["hot"])
