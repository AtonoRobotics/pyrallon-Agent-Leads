"""Inbound-only voice boundary primitives.

The voice provider may deliver untrusted form fields, so parsing, signature
verification, AI disclosure, and recording consent are explicit operations.
This module never places an outbound call and never treats recording as implied
by participation in a call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
from dataclasses import dataclass
from urllib.parse import parse_qs


class VoiceWebhookError(ValueError):
    """A voice webhook is malformed or cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class InboundVoiceEvent:
    call_sid: str
    from_number: str
    to_number: str
    call_status: str
    recording_status: str | None
    recording_url: str | None
    caller_name: str | None


def parse_twilio_form(body: bytes) -> dict[str, str]:
    """Parse Twilio's application/x-www-form-urlencoded webhook body."""
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VoiceWebhookError("voice webhook body is not UTF-8") from exc
    parsed = parse_qs(decoded, keep_blank_values=False, strict_parsing=True)
    result: dict[str, str] = {}
    for key, values in parsed.items():
        if len(values) != 1 or not values[0]:
            raise VoiceWebhookError("voice webhook fields must have one non-empty value")
        result[key] = values[0]
    return result


def verify_twilio_signature(
    *, auth_token: bytes, webhook_url: str, params: dict[str, str], signature: str
) -> bool:
    """Verify X-Twilio-Signature over the exact public URL and sorted form fields."""
    if not auth_token or not webhook_url or not signature:
        return False
    material = webhook_url + "".join(key + params[key] for key in sorted(params))
    expected = base64.b64encode(
        hmac.new(auth_token, material.encode(), hashlib.sha1).digest()
    ).decode()
    return hmac.compare_digest(expected, signature.strip())


def inbound_voice_event(params: dict[str, str]) -> InboundVoiceEvent:
    required = ("CallSid", "From", "To", "CallStatus")
    if any(not params.get(key) for key in required):
        raise VoiceWebhookError("voice webhook requires CallSid, From, To, and CallStatus")
    return InboundVoiceEvent(
        call_sid=params["CallSid"],
        from_number=params["From"],
        to_number=params["To"],
        call_status=params["CallStatus"],
        recording_status=params.get("RecordingStatus"),
        recording_url=params.get("RecordingUrl"),
        caller_name=params.get("CallerName"),
    )


def render_inbound_ai_disclosure(*, agent_name: str, brokerage_name: str) -> str:
    """Return TwiML that identifies the AI and leaves recording disabled."""
    agent = html.escape(agent_name.strip(), quote=True)
    brokerage = html.escape(brokerage_name.strip(), quote=True)
    if not agent or not brokerage:
        raise VoiceWebhookError("agent and brokerage names are required for AI disclosure")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<Response><Say>You've reached "
        + agent
        + "'s AI assistant for "
        + brokerage
        + ". This is an artificial intelligence assistant. "
        + "This call is not being recorded. How may I help?</Say></Response>"
    )


def recording_transition(*, current: str, affirmative: bool) -> str:
    """Apply an explicit recording answer; participation never grants consent."""
    if current not in {"not_requested", "refused", "consented", "revoked"}:
        raise VoiceWebhookError("recording state is invalid")
    if current == "revoked":
        raise VoiceWebhookError("recording consent cannot be revived after revocation")
    return "consented" if affirmative else "refused"
