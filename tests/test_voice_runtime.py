import base64
import hashlib
import hmac

import pytest

from buyer_ops_contracts.voice_runtime import (
    VoiceWebhookError,
    inbound_voice_event,
    parse_twilio_form,
    recording_transition,
    render_inbound_ai_disclosure,
    verify_twilio_signature,
)


def test_twilio_voice_signature_is_verified_over_sorted_form_fields():
    url = "https://voice.example.test/v1/ingress/webhook/primary-phone"
    params = {"CallSid": "CA-1", "From": "+15125550100", "To": "+15125550101"}
    material = url + "".join(key + params[key] for key in sorted(params))
    signature = base64.b64encode(
        hmac.new(b"a" * 32, material.encode(), hashlib.sha1).digest()
    ).decode()

    assert verify_twilio_signature(
        auth_token=b"a" * 32, webhook_url=url, params=params, signature=signature
    )
    assert not verify_twilio_signature(
        auth_token=b"a" * 32, webhook_url=url, params=params, signature="wrong"
    )


def test_voice_form_requires_single_values_and_normalizes_call_event():
    params = parse_twilio_form(
        b"CallSid=CA-1&From=%2B15125550100&To=%2B15125550101&CallStatus=ringing"
    )
    event = inbound_voice_event(params)
    assert event.call_sid == "CA-1"
    assert event.from_number == "+15125550100"
    with pytest.raises(VoiceWebhookError):
        parse_twilio_form(b"CallSid=CA-1&CallSid=CA-2")


def test_inbound_ai_disclosure_identifies_ai_and_recording_is_off():
    twiml = render_inbound_ai_disclosure(agent_name="Alex", brokerage_name="Example Realty")
    assert "AI assistant" in twiml
    assert "not being recorded" in twiml
    assert recording_transition(current="not_requested", affirmative=False) == "refused"
    assert recording_transition(current="not_requested", affirmative=True) == "consented"
    with pytest.raises(VoiceWebhookError, match="revocation"):
        recording_transition(current="revoked", affirmative=True)
