"""
R77 Integrity Tests.
"""

import json
import os
import shutil
import tempfile
import unittest

from services.integrity import (
    IntegrityEnvelope,
    IntegrityError,
    calculate_hash,
    canonical_dumps,
    load_verified,
    save_verified,
)


class TestR77Integrity(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_canonical_serialization(self):
        """Test that JSON is canonicalized (sorted keys, no whitespace)."""
        data1 = {"b": 2, "a": 1}
        data2 = {"a": 1, "b": 2}

        c1 = canonical_dumps(data1)
        c2 = canonical_dumps(data2)

        self.assertEqual(c1, c2)
        # Check format: {"a":1,"b":2}
        self.assertEqual(c1, b'{"a":1,"b":2}')

    def test_hashing(self):
        """Test hash consistency."""
        data = {"foo": "bar"}
        h1 = calculate_hash(data)
        h2 = calculate_hash(data)
        self.assertEqual(h1, h2)
        self.assertTrue(len(h1) == 64)  # SHA256 hex digest

    def test_save_and_load_verified(self):
        """Test saving and loading with integrity envelope."""
        path = os.path.join(self.test_dir, "test.json")
        data = {"key": "value", "list": [1, 2, 3]}

        save_verified(path, data)

        # Verify file structure on disk
        with open(path, "r") as f:
            envelope = json.load(f)

        self.assertIn("version", envelope)
        self.assertIn("hash", envelope)
        self.assertIn("data", envelope)
        self.assertEqual(envelope["data"], data)

        # Load back
        loaded = load_verified(path)
        self.assertEqual(loaded, data)

    def test_tamper_detection(self):
        """Test that modification of data voids the integrity check."""
        path = os.path.join(self.test_dir, "tampered.json")
        data = {"secret": "123"}
        save_verified(path, data)

        # Tamper with the file
        with open(path, "r") as f:
            envelope = json.load(f)

        envelope["data"]["secret"] = "666"  # Evil modification
        # Hash is NOT updated

        with open(path, "w") as f:
            json.dump(envelope, f)

        # Load should fail
        with self.assertRaises(IntegrityError):
            load_verified(path)

    def test_legacy_migration(self):
        """Test verifying legacy (non-envelope) files."""
        path = os.path.join(self.test_dir, "legacy.json")
        data = {"old": "data"}

        # Save as raw JSON
        with open(path, "w") as f:
            json.dump(data, f)

        # load_verified with migrate=True (default) should succeed
        loaded = load_verified(path, migrate=True)
        self.assertEqual(loaded, data)

        # load_verified with migrate=False should fail
        with self.assertRaises(IntegrityError):
            load_verified(path, migrate=False)

    def test_save_verified_rejects_invalid_leaf_target(self):
        with self.assertRaises(IntegrityError):
            save_verified("", {"k": "v"})


if __name__ == "__main__":
    unittest.main()
