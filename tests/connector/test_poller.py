import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from connector.config import ConnectorConfig
from connector.contract import Platform
from connector.results_poller import ResultsPoller


class MockPlatform(Platform):
    async def send_image(
        self,
        channel_id,
        image_data,
        filename="image.png",
        caption=None,
        delivery_context=None,
    ):
        pass

    async def send_message(self, channel_id, text, delivery_context=None):
        pass


class TestResultsPoller(unittest.TestCase):
    def setUp(self):
        self.config = ConnectorConfig()
        self.config.delivery_timeout_sec = 5  # short timeout for tests

        self.client = MagicMock()
        self.client.get_history = AsyncMock()
        self.client.get_view = AsyncMock()

        self.mock_platform = MockPlatform()
        self.mock_platform.send_image = AsyncMock()
        self.mock_platform.send_message = AsyncMock()

        self.platforms = {"test_plat": self.mock_platform}
        self.poller = ResultsPoller(self.config, self.client, self.platforms)

    def test_track_job(self):
        self.poller.track_job("p-1", "test_plat", "c-1", "u-1")
        self.assertEqual(self.poller.queue.qsize(), 1)
        item = self.poller.queue.get_nowait()
        self.assertEqual(item, ("p-1", "test_plat", "c-1", "u-1", {}))

    @patch("connector.results_poller.time")
    @patch("connector.results_poller.asyncio.sleep", new_callable=AsyncMock)
    def test_poll_job_success(self, mock_sleep, mock_time):
        mock_time.time.side_effect = [0, 1, 2, 3]

        self.client.get_history.side_effect = [
            {"ok": True, "data": {}},
            {
                "ok": True,
                "data": {
                    "p-1": {
                        "outputs": {
                            "node-1": {
                                "images": [{"filename": "f.png", "type": "output"}]
                            }
                        }
                    }
                },
            },
        ]

        self.client.get_view.return_value = b"image_bytes"

        asyncio.run(
            self.poller._poll_job(
                "p-1",
                "test_plat",
                "c-1",
                "u-1",
                {"workspace_id": "T1", "thread_id": "123.456"},
            )
        )

        self.assertEqual(self.client.get_history.call_count, 2)
        self.client.get_view.assert_called_with("f.png", "", "output")
        self.mock_platform.send_image.assert_called_with(
            "c-1",
            b"image_bytes",
            filename="f.png",
            delivery_context={"workspace_id": "T1", "thread_id": "123.456"},
        )

    @patch("connector.results_poller.time")
    @patch("connector.results_poller.asyncio.sleep", new_callable=AsyncMock)
    def test_poll_job_no_outputs(self, mock_sleep, mock_time):
        # Scenario: Job finished, but "outputs" is empty or has no images.
        mock_time.time.side_effect = [0, 1, 2]

        self.client.get_history.side_effect = [
            {
                "ok": True,
                "data": {"p-empty": {"outputs": {}}},
            }  # Completed, empty outputs
        ]

        asyncio.run(self.poller._poll_job("p-empty", "test_plat", "c-1", "u-1"))

        self.mock_platform.send_image.assert_not_called()
        self.mock_platform.send_message.assert_called_once()
        args = self.mock_platform.send_message.call_args
        self.assertIn("No output images", args[0][1])

    @patch("connector.results_poller.time")
    @patch("connector.results_poller.asyncio.sleep", new_callable=AsyncMock)
    def test_poll_job_timeout(self, mock_sleep, mock_time):
        self.config.delivery_timeout_sec = 2
        mock_time.time.side_effect = [0, 1, 3]

        self.client.get_history.return_value = {"ok": True, "data": {}}

        asyncio.run(self.poller._poll_job("p-timeout", "test_plat", "c-1", "u-1"))

        self.mock_platform.send_image.assert_not_called()
        self.mock_platform.send_message.assert_called_once()
        self.assertIn("timed out", self.mock_platform.send_message.call_args[0][1])

    def test_deliver_results_limits(self):
        self.config.delivery_max_images = 1

        job_data = {
            "outputs": {
                "n1": {
                    "images": [
                        {"filename": "1.png", "type": "output"},
                        {"filename": "2.png", "type": "output"},
                    ]
                }
            }
        }
        self.client.get_view.return_value = b"123"

        asyncio.run(self.poller._deliver_results("p-1", job_data, "test_plat", "c-1"))

        self.assertEqual(self.mock_platform.send_image.call_count, 1)
        args = self.mock_platform.send_image.call_args
        self.assertEqual(args[1]["filename"], "1.png")

    def test_deliver_results_send_failure(self):
        # Scenario: Platform.send_image raises Exception
        job_data = {
            "outputs": {"n1": {"images": [{"filename": "f.png", "type": "output"}]}}
        }
        self.client.get_view.return_value = b"bytes"
        self.mock_platform.send_image.side_effect = Exception("Network Error")

        asyncio.run(
            self.poller._deliver_results("p-fail", job_data, "test_plat", "c-1")
        )

        # Should catch exception and try sending fallback text
        self.mock_platform.send_message.assert_called()
        self.assertIn(
            "Failed to send image", self.mock_platform.send_message.call_args[0][1]
        )


if __name__ == "__main__":
    unittest.main()
