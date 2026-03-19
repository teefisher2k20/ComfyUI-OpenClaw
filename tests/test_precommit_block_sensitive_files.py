import unittest

import scripts.precommit_block_sensitive_files as mod


class PrecommitBlockSensitiveFilesTests(unittest.TestCase):
    def test_planning_paths_are_blocked(self):
        staged = [
            ".planning/roadmap.md",
            ".planning/roadmap/open/ROBUSTNESS_OPEN.md",
            "tests/TEST_SOP.md",
        ]

        self.assertEqual(
            mod._get_blocked_paths(staged),
            [
                ".planning/roadmap.md",
                ".planning/roadmap/open/ROBUSTNESS_OPEN.md",
            ],
        )

    def test_root_roadmap_stub_is_allowed(self):
        staged = [
            "ROADMAP.md",
            "README.md",
        ]

        self.assertEqual(mod._get_blocked_paths(staged), [])


if __name__ == "__main__":
    unittest.main()
