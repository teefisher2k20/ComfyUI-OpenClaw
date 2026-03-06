import unittest
from unittest.mock import patch

from services import security_telemetry


class TestR102SecurityTelemetry(unittest.TestCase):
    def setUp(self):
        self.telemetry = security_telemetry.SecurityTelemetry()

    def _event_factory(self, event_type, payload, meta):
        return {"event_type": event_type, "payload": payload, "meta": meta}

    def test_anomaly_schema_keys(self):
        event = security_telemetry.AnomalyEvent(
            code="SEC-001",
            severity="medium",
            source="auth_module:127.0.0.1",
            count=10,
            window=60,
            action="alert",
        )
        keys = set(event.to_dict().keys())
        self.assertEqual(
            keys, {"code", "severity", "source", "count", "window", "action"}
        )

    def test_auth_failure_spike_triggers_alert(self):
        with (
            patch(
                "services.security_telemetry.build_audit_event",
                side_effect=self._event_factory,
            ),
            patch("services.security_telemetry.emit_audit_event") as emit,
        ):
            for _ in range(
                security_telemetry.THRESHOLDS[
                    security_telemetry.ANOMALY_AUTH_FAILURE_SPIKE
                ]["count"]
            ):
                self.telemetry.record_auth_failure("127.0.0.1")

            self.assertGreaterEqual(emit.call_count, 1)
            payload = emit.call_args.args[0]["payload"]
            self.assertEqual(
                payload["code"], security_telemetry.ANOMALY_AUTH_FAILURE_SPIKE
            )

    def test_replay_burst_triggers_block_action(self):
        with (
            patch(
                "services.security_telemetry.build_audit_event",
                side_effect=self._event_factory,
            ),
            patch("services.security_telemetry.emit_audit_event") as emit,
        ):
            for _ in range(
                security_telemetry.THRESHOLDS[security_telemetry.ANOMALY_REPLAY_BURST][
                    "count"
                ]
            ):
                self.telemetry.record_replay_rejection("webhook")

            payload = emit.call_args.args[0]["payload"]
            self.assertEqual(payload["code"], security_telemetry.ANOMALY_REPLAY_BURST)
            self.assertEqual(payload["action"], "block")

    def test_override_and_queue_events_emit(self):
        with (
            patch(
                "services.security_telemetry.build_audit_event",
                side_effect=self._event_factory,
            ),
            patch("services.security_telemetry.emit_audit_event") as emit,
        ):
            self.telemetry.record_dangerous_override("OVERRIDE_X", "tester")
            self.telemetry.record_queue_saturation(1500)
            codes = [call.args[0]["payload"]["code"] for call in emit.call_args_list]
            self.assertIn(security_telemetry.ANOMALY_DANGEROUS_OVERRIDE, codes)
            self.assertIn(security_telemetry.ANOMALY_QUEUE_SATURATION, codes)

    def test_telemetry_opt_out_disables_emission(self):
        with (
            patch.dict("os.environ", {"OPENCLAW_TELEMETRY_OPT_OUT": "1"}),
            patch(
                "services.security_telemetry.build_audit_event",
                side_effect=self._event_factory,
            ),
            patch("services.security_telemetry.emit_audit_event") as emit,
        ):
            self.telemetry.record_dangerous_override("OVERRIDE_X", "tester")
            self.telemetry.record_queue_saturation(1500)
            self.assertEqual(emit.call_count, 0)


if __name__ == "__main__":
    unittest.main()
