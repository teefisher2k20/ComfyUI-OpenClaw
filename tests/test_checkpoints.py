import json
import os
import shutil
import time
import unittest
from unittest.mock import MagicMock, patch

from services import checkpoints
from services.checkpoints import (
    create_checkpoint,
    delete_checkpoint,
    get_checkpoint,
    list_checkpoints,
)

# Use a temp dir for testing
TEST_DATA_DIR = os.path.join(os.path.dirname(__file__), "test_data_checkpoints")


class TestCheckpointsService(unittest.TestCase):

    def setUp(self):
        # Override DATA_DIR for tests
        self.original_dir = checkpoints.CHECKPOINTS_DIR
        checkpoints.CHECKPOINTS_DIR = os.path.join(TEST_DATA_DIR, "checkpoints")
        if os.path.exists(TEST_DATA_DIR):
            shutil.rmtree(TEST_DATA_DIR)
        os.makedirs(checkpoints.CHECKPOINTS_DIR)

    def tearDown(self):
        checkpoints.CHECKPOINTS_DIR = self.original_dir
        if os.path.exists(TEST_DATA_DIR):
            shutil.rmtree(TEST_DATA_DIR)

    def test_crud(self):
        # Create
        workflow = {"1": {"class_type": "Node"}}
        meta = create_checkpoint("Test 1", workflow, "Desc 1")
        self.assertIsNotNone(meta["id"])
        self.assertEqual(meta["name"], "Test 1")

        # List
        lst = list_checkpoints()
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["id"], meta["id"])

        # Get
        full = get_checkpoint(meta["id"])
        self.assertEqual(full["id"], meta["id"])
        self.assertEqual(full["workflow"], workflow)

        # Delete
        delete_checkpoint(meta["id"])
        self.assertEqual(len(list_checkpoints()), 0)
        self.assertIsNone(get_checkpoint(meta["id"]))

    def test_eviction(self):
        # Reduce limit for test
        original_max = checkpoints.MAX_CHECKPOINTS
        checkpoints.MAX_CHECKPOINTS = 2

        try:
            # Create 3
            meta1 = create_checkpoint("1", {})
            time.sleep(0.01)  # ensure timestamp diff
            meta2 = create_checkpoint("2", {})
            time.sleep(0.01)
            meta3 = create_checkpoint("3", {})

            lst = list_checkpoints()
            self.assertEqual(len(lst), 2)

            ids = [x["id"] for x in lst]
            self.assertIn(meta3["id"], ids)
            self.assertIn(meta2["id"], ids)
            self.assertNotIn(meta1["id"], ids)  # Oldest evicted

        finally:
            checkpoints.MAX_CHECKPOINTS = original_max

    def test_size_limit(self):
        # 2MB string
        big_workflow = {"data": "x" * (2 * 1024 * 1024)}
        with self.assertRaises(ValueError):
            create_checkpoint("Too Big", big_workflow)

    def test_validation(self):
        # Name too long
        long_name = "x" * 101
        with self.assertRaises(ValueError):
            create_checkpoint(long_name, {})

        # Description too long
        long_desc = "x" * 501
        with self.assertRaises(ValueError):
            create_checkpoint("Valid Name", {}, long_desc)

    def test_invalid_checkpoint_ids_fail_closed(self):
        self.assertIsNone(get_checkpoint("../escape"))
        self.assertIsNone(get_checkpoint("not-a-uuid"))
        self.assertFalse(delete_checkpoint("../escape"))
        self.assertFalse(delete_checkpoint("not-a-uuid"))


if __name__ == "__main__":
    unittest.main()
