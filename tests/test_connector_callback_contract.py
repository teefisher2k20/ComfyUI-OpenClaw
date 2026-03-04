import tempfile
import time
import unittest

from connector.config import CommandClass
from services.connector_callback_contract import (
    CallbackActorContext,
    CallbackDecisionCode,
    ConnectorCallbackContract,
)
from services.connector_installation_registry import (
    ConnectorInstallationRegistry,
    InstallationStatus,
)
from services.secret_store import SecretStore


class TestConnectorCallbackContract(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        state_dir = self.tmpdir.name
        self.registry = ConnectorInstallationRegistry(
            state_dir=state_dir,
            secret_store=SecretStore(state_dir=state_dir),
        )
        self.registry.upsert_installation(
            platform="slack",
            workspace_id="T1",
            installation_id="inst-1",
            token_values={"bot_token": "xoxb-abc"},
            status=InstallationStatus.ACTIVE.value,
        )
        self.contract = ConnectorCallbackContract(
            signing_secret="signing-secret",
            installation_registry=self.registry,
            action_policy_map={
                "action.status": CommandClass.PUBLIC.value,
                "action.run": CommandClass.RUN.value,
                "action.admin": CommandClass.ADMIN.value,
                "action.workflow.*": CommandClass.RUN.value,
            },
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _payload(self):
        return {"button": "go", "value": 1}

    def test_valid_public_callback_accepts(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-1",
            workspace_id="T1",
            action_type="action.status",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(is_admin=False, is_trusted=False),
        )
        self.assertTrue(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.ACCEPT_PUBLIC.value
        )
        self.assertTrue(decision.callback_id)

    def test_tampered_signature_rejected(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-2",
            workspace_id="T1",
            action_type="action.status",
            payload=payload,
        )
        envelope.signature = "bad-signature"
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        self.assertFalse(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.REJECT_SIGNATURE.value
        )

    def test_stale_timestamp_rejected(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-3",
            workspace_id="T1",
            action_type="action.status",
            payload=payload,
            timestamp=int(time.time()) - 1000,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        self.assertFalse(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.REJECT_TIMESTAMP.value
        )

    def test_replay_request_id_rejected(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-replay",
            workspace_id="T1",
            action_type="action.status",
            payload=payload,
        )
        first = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        second = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.decision_code, CallbackDecisionCode.REJECT_REPLAY.value)

    def test_payload_hash_mismatch_rejected(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-hash",
            workspace_id="T1",
            action_type="action.status",
            payload=payload,
        )
        tampered_payload = {"button": "go", "value": 2}
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=tampered_payload,
            actor=CallbackActorContext(),
        )
        self.assertFalse(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.REJECT_PAYLOAD_HASH.value
        )

    def test_unknown_action_rejected(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-unknown",
            workspace_id="T1",
            action_type="action.unknown",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        self.assertFalse(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.REJECT_UNKNOWN_ACTION.value
        )

    def test_policy_matrix_run_requires_approval_for_untrusted(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-run",
            workspace_id="T1",
            action_type="action.run",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(is_admin=False, is_trusted=False),
        )
        self.assertFalse(decision.ok)
        self.assertTrue(decision.requires_approval)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.REQUIRE_APPROVAL.value
        )

    def test_policy_matrix_run_accepts_for_trusted(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-run-trusted",
            workspace_id="T1",
            action_type="action.workflow.deploy",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(is_trusted=True),
        )
        self.assertTrue(decision.ok)
        self.assertEqual(decision.decision_code, CallbackDecisionCode.ACCEPT_RUN.value)

    def test_policy_matrix_admin_denied_for_non_admin(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-admin-deny",
            workspace_id="T1",
            action_type="action.admin",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(is_admin=False),
        )
        self.assertFalse(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.REJECT_POLICY_DENIED.value
        )

    def test_policy_matrix_admin_accepts_for_admin(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-admin-allow",
            workspace_id="T1",
            action_type="action.admin",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(is_admin=True),
        )
        self.assertTrue(decision.ok)
        self.assertEqual(
            decision.decision_code, CallbackDecisionCode.ACCEPT_ADMIN.value
        )

    def test_missing_installation_rejected(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-missing-install",
            workspace_id="T-missing",
            action_type="action.status",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        self.assertFalse(decision.ok)
        self.assertEqual(
            decision.decision_code,
            CallbackDecisionCode.REJECT_MISSING_INSTALLATION.value,
        )

    def test_ack_and_deferred_delivery_lifecycle(self):
        payload = self._payload()
        envelope = self.contract.build_envelope(
            request_id="req-ack",
            workspace_id="T1",
            action_type="action.status",
            payload=payload,
        )
        decision = self.contract.evaluate(
            platform="slack",
            envelope_dict=envelope.__dict__,
            payload=payload,
            actor=CallbackActorContext(),
        )
        self.assertTrue(decision.ok)
        record = self.contract.get_record("req-ack")
        self.assertIsNotNone(record)
        self.assertEqual(record.state, "pending")

        acked = self.contract.acknowledge_request("req-ack")
        self.assertEqual(acked.state, "acknowledged")

        delivered = self.contract.complete_request("req-ack")
        self.assertEqual(delivered.state, "delivered")


if __name__ == "__main__":
    unittest.main()
