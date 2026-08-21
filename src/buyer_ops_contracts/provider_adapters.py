"""Direct production provider adapters behind the governed connector boundary.

These adapters own provider transport and credentials.  They deliberately accept
only the already-authorized connector request and effect payload produced by the
Habitat/connector gateway; they cannot write canonical state or mint authority.
The transport is injectable for deterministic contract tests and defaults to
HTTPS JSON/form transport in production.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .connector_gateway import ConnectorAdapter
from .workload_provider_credentials import ProviderWorkloadCredential, ProviderWorkloadIdentity


class ProviderAdapterError(RuntimeError):
    """A provider failure with a safe, retry-aware diagnostic."""

    def __init__(self, code: str, detail: str, *, retryable: bool) -> None:
        self.code = code
        self.detail = detail
        self.retryable = retryable
        super().__init__(detail)


class ProviderTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Mapping[str, str], bytes]: ...


class CredentialResolver(Protocol):
    def token(self) -> str: ...


ProviderResult = tuple[int, Mapping[str, str], dict[str, Any]]


class UrllibProviderTransport:
    """The production HTTPS transport; no provider SDK is allowed past this edge."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return (
                    int(response.status),
                    cast(Mapping[str, str], response.headers),
                    response.read(),
                )
        except urllib.error.HTTPError as exc:
            return int(exc.code), cast(Mapping[str, str], exc.headers), exc.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProviderAdapterError(
                "provider_unavailable", "provider transport unavailable", retryable=True
            ) from exc


@dataclass(frozen=True, slots=True)
class DirectProviderConfig:
    connector_id: str
    provider: str
    credential_env: str
    account_id: str | None = None
    api_base: str | None = None
    timeout_seconds: float = 20.0
    workload_identity: ProviderWorkloadIdentity | None = None

    @classmethod
    def from_value(cls, value: Any) -> DirectProviderConfig:
        if not isinstance(value, dict):
            raise ValueError("direct provider configuration must be an object")
        required = ("connectorId", "provider", "credentialEnv")
        if any(not isinstance(value.get(field), str) or not value[field] for field in required):
            raise ValueError(
                "direct provider configuration requires connectorId, provider, credentialEnv"
            )
        timeout = float(value.get("timeoutSeconds", 20))
        if not 0 < timeout <= 60:
            raise ValueError("direct provider timeoutSeconds must be between 0 and 60")
        provider = {
            "google": "google_calendar",
            "microsoft": "microsoft_graph",
        }.get(str(value["provider"]).lower(), str(value["provider"]).lower())
        if provider not in {"google_calendar", "microsoft_graph", "twilio", "sendgrid", "docusign"}:
            raise ValueError(f"unsupported direct provider: {provider}")
        base = value.get("apiBase")
        if base is not None and (not isinstance(base, str) or not base.startswith("https://")):
            raise ValueError("direct provider apiBase must use HTTPS")
        mode = value.get("credentialMode", "static")
        if not isinstance(mode, str):
            raise ValueError("credentialMode must be a string")
        mode = mode.strip().lower()
        workload_identity = None
        if mode != "static":
            workload_value = dict(value)
            workload_value["provider"] = provider
            workload_identity = ProviderWorkloadIdentity.from_value(workload_value)
        return cls(
            connector_id=str(value["connectorId"]),
            provider=provider,
            credential_env=str(value["credentialEnv"]),
            account_id=str(value["accountId"]) if value.get("accountId") else None,
            api_base=base,
            timeout_seconds=timeout,
            workload_identity=workload_identity,
        )


class DirectProviderAdapter(ConnectorAdapter):
    """Provider-specific execution with receipt/idempotency binding."""

    def __init__(
        self,
        config: DirectProviderConfig,
        *,
        transport: ProviderTransport | None = None,
        credential: str | None = None,
        credential_resolver: CredentialResolver | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or UrllibProviderTransport()
        self._credential_resolver = credential_resolver
        if self._credential_resolver is None and config.workload_identity is not None:
            self._credential_resolver = ProviderWorkloadCredential(config.workload_identity)
        self._credential = credential if credential is not None else os.environ.get(config.credential_env, "")
        if self._credential_resolver is None and len(self._credential) < 16:
            raise ValueError(
                f"provider credential env {config.credential_env} is missing or too short"
            )

    def invoke(self, request: dict[str, Any], payload: bytes) -> dict[str, Any]:
        try:
            body = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderAdapterError(
                "payload_invalid", "provider payload must be JSON", retryable=False
            ) from exc
        if not isinstance(body, dict):
            raise ProviderAdapterError(
                "payload_invalid", "provider payload must be an object", retryable=False
            )
        action = str(body.get("action") or request.get("capability") or "")
        if not action:
            raise ProviderAdapterError(
                "payload_invalid", "provider action is required", retryable=False
            )
        status, headers, result = self._dispatch(action, body, request)
        receipt = str(
            result.get("id")
            or result.get("sid")
            or result.get("messageId")
            or result.get("envelopeId")
            or headers.get("x-message-id", "")
        )
        if not receipt:
            receipt = str(request["requestId"])
        outcome = "confirmed" if 200 <= status < 300 else "rejected"
        if status >= 500 or status == 429:
            outcome = "unknown"
        elif status in {401, 403}:
            outcome = "revoked"
        elif status == 409:
            outcome = "conflict"
        return {
            "messageType": "connector_response",
            "schemaVersion": "connector-gateway/1.0.0",
            "tenantId": request["tenantId"],
            "connectorId": request["connectorId"],
            "grantId": request["grantId"],
            "grantVersion": request["grantVersion"],
            "capability": request["capability"],
            "delegatedPrincipalId": request["delegatedPrincipalId"],
            "correlationId": request["correlationId"],
            "requestId": request["requestId"],
            "payloadDigest": request["payloadDigest"],
            "receiptId": receipt,
            "outcome": outcome,
            "providerVersion": str(headers.get("x-api-version") or self.config.provider),
            "providerResponse": _safe_provider_response(result),
        }

    def reconcile(self, request: dict[str, Any], provider_receipt_id: str) -> dict[str, Any]:
        """Read provider truth for an ambiguous effect without issuing a new effect."""
        if not provider_receipt_id:
            raise ValueError("provider receipt is required for reconciliation")
        action = str(request.get("providerAction") or request.get("capability") or "")
        status, headers, result = self._dispatch(
            f"{action}.get", {"id": provider_receipt_id, "action": f"{action}.get"}, request
        )
        terminal = 200 <= status < 300
        safe_response = _safe_provider_response(result)
        return {
            "attemptState": "reconciled_succeeded" if terminal else "reconciled_failed",
            "providerReceiptId": provider_receipt_id,
            "providerStatus": status,
            "providerVersion": str(headers.get("x-api-version") or self.config.provider),
            "providerResponse": safe_response,
        }

    def _dispatch(
        self, action: str, body: dict[str, Any], request: dict[str, Any]
    ) -> tuple[int, Mapping[str, str], dict[str, Any]]:
        provider = self.config.provider
        if provider == "google_calendar":
            return self._google(action, body, request)
        if provider == "microsoft_graph":
            return self._microsoft(action, body, request)
        if provider == "twilio":
            return self._twilio(action, body, request)
        if provider == "sendgrid":
            return self._sendgrid(action, body, request)
        return self._docusign(action, body, request)

    def _google(self, action: str, body: dict[str, Any], request: dict[str, Any]) -> ProviderResult:
        base = self.config.api_base or "https://www.googleapis.com/calendar/v3"
        calendar_id = urllib.parse.quote(
            str(body.get("calendarId") or self.config.account_id or "primary"), safe=""
        )
        if action.endswith("freebusy") or action == "calendar.availability":
            return self._json(
                "POST",
                f"{base}/freeBusy",
                {
                    "timeMin": body["timeMin"],
                    "timeMax": body["timeMax"],
                    "items": [
                        {"id": str(body.get("calendarId") or self.config.account_id or "primary")}
                    ],
                },
                request,
            )
        event_id = urllib.parse.quote(str(body.get("id", "")), safe="")
        if action.endswith("create") or action == "calendar.book":
            return self._json(
                "POST",
                f"{base}/calendars/{calendar_id}/events",
                _google_event_payload(body),
                request,
            )
        if action in {"calendar.reschedule", "calendar.update"} or action.endswith("update"):
            return self._json(
                "PATCH",
                f"{base}/calendars/{calendar_id}/events/{event_id}",
                _google_event_payload(body),
                request,
            )
        if action.endswith("cancel"):
            return self._json(
                "DELETE", f"{base}/calendars/{calendar_id}/events/{event_id}", None, request
            )
        return self._json("GET", f"{base}/calendars/{calendar_id}/events/{event_id}", None, request)

    def _microsoft(
        self, action: str, body: dict[str, Any], request: dict[str, Any]
    ) -> ProviderResult:
        account = urllib.parse.quote(
            str(body.get("accountId") or self.config.account_id or "me"), safe=""
        )
        base = self.config.api_base or "https://graph.microsoft.com/v1.0"
        if action.endswith("freebusy") or action == "calendar.availability":
            return self._json(
                "POST",
                f"{base}/users/{account}/calendar/getSchedule",
                {
                    "schedules": [str(body.get("calendarId") or self.config.account_id or account)],
                    "startTime": {
                        "dateTime": body["timeMin"],
                        "timeZone": str(body.get("timeZone") or "UTC"),
                    },
                    "endTime": {
                        "dateTime": body["timeMax"],
                        "timeZone": str(body.get("timeZone") or "UTC"),
                    },
                    "availabilityViewInterval": 30,
                },
                request,
            )
        event_id = urllib.parse.quote(str(body.get("id", "")), safe="")
        if action.endswith("create") or action == "calendar.book":
            return self._json(
                "POST",
                f"{base}/users/{account}/events",
                _microsoft_event_payload(body),
                request,
            )
        if action in {"calendar.reschedule", "calendar.update"} or action.endswith("update"):
            return self._json(
                "PATCH",
                f"{base}/users/{account}/events/{event_id}",
                _microsoft_event_payload(body),
                request,
            )
        if action.endswith("cancel"):
            return self._json("DELETE", f"{base}/users/{account}/events/{event_id}", None, request)
        return self._json("GET", f"{base}/users/{account}/events/{event_id}", None, request)

    def _twilio(self, action: str, body: dict[str, Any], request: dict[str, Any]) -> ProviderResult:
        if action not in {"sms.send", "message.send"}:
            raise ProviderAdapterError(
                "action_unsupported", "Twilio supports only SMS send", retryable=False
            )
        try:
            account, token = self._credential.split(":", 1)
        except ValueError as exc:
            raise ProviderAdapterError(
                "credential_invalid",
                "Twilio credential must be accountSid:authToken",
                retryable=False,
            ) from exc
        base = self.config.api_base or "https://api.twilio.com/2010-04-01"
        url = f"{base}/Accounts/{urllib.parse.quote(account, safe='')}/Messages.json"
        encoded = urllib.parse.urlencode(
            {"To": body["to"], "From": body["from"], "Body": body["text"]}
        ).encode()
        auth = base64.b64encode(f"{account}:{token}".encode()).decode()
        return self._request(
            "POST",
            url,
            encoded,
            request,
            content_type="application/x-www-form-urlencoded",
            authorization=f"Basic {auth}",
        )

    def _sendgrid(
        self, action: str, body: dict[str, Any], request: dict[str, Any]
    ) -> ProviderResult:
        if action not in {"email.send", "message.send"}:
            raise ProviderAdapterError(
                "action_unsupported", "SendGrid supports only email send", retryable=False
            )
        base = self.config.api_base or "https://api.sendgrid.com"
        return self._json("POST", f"{base}/v3/mail/send", _sendgrid_mail_payload(body), request)

    def _docusign(
        self, action: str, body: dict[str, Any], request: dict[str, Any]
    ) -> ProviderResult:
        account = urllib.parse.quote(
            str(body.get("accountId") or self.config.account_id or ""), safe=""
        )
        if not account:
            raise ProviderAdapterError(
                "configuration_incomplete", "DocuSign accountId is required", retryable=False
            )
        base = self.config.api_base or "https://www.docusign.net/restapi/v2.1"
        envelope = urllib.parse.quote(str(body.get("id", "")), safe="")
        if action in {"esign.create", "agreement.send"}:
            return self._json(
                "POST",
                f"{base}/accounts/{account}/envelopes",
                _docusign_envelope_payload(body),
                request,
            )
        if action in {"esign.void", "agreement.void"}:
            return self._json(
                "PUT",
                f"{base}/accounts/{account}/envelopes/{envelope}",
                {"status": "voided", "voidedReason": body.get("voidedReason", "buyer-ops-request")},
                request,
            )
        return self._json("GET", f"{base}/accounts/{account}/envelopes/{envelope}", None, request)

    def _json(
        self, method: str, url: str, payload: dict[str, Any] | None, request: dict[str, Any]
    ) -> ProviderResult:
        body = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        )
        return self._request(method, url, body, request, content_type="application/json")

    def _request(
        self,
        method: str,
        url: str,
        body: bytes | None,
        request: dict[str, Any],
        *,
        content_type: str,
        authorization: str | None = None,
    ) -> ProviderResult:
        headers = {
            "Accept": "application/json",
            "Content-Type": content_type,
            "Idempotency-Key": str(request["idempotencyKey"]),
            "X-Buyer-Ops-Request-Id": str(request["requestId"]),
        }
        if authorization is not None:
            headers["Authorization"] = authorization
        elif self.config.provider == "twilio":
            pass
        else:
            headers["Authorization"] = f"Bearer {self._access_token()}"
        status, response_headers, raw = self._transport.request(
            method, url, headers=headers, body=body, timeout=self.config.timeout_seconds
        )
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderAdapterError(
                "provider_response_invalid", "provider returned invalid JSON", retryable=False
            ) from exc
        if not isinstance(decoded, dict):
            decoded = {"value": decoded}
        if status >= 400:
            raise ProviderAdapterError(
                "provider_rejected",
                f"provider returned HTTP {status}",
                retryable=status == 429 or status >= 500,
            )
        return status, response_headers, decoded


    def _access_token(self) -> str:
        if self._credential_resolver is None:
            return self._credential
        token = self._credential_resolver.token()
        if len(token) < 16:
            raise ProviderAdapterError(
                "credential_invalid", "provider workload credential is invalid", retryable=False
            )
        return token


def _safe_provider_response(value: dict[str, Any]) -> dict[str, Any]:
    """Exclude credentials and authorization material from canonical responses."""
    forbidden = {"access_token", "refresh_token", "authorization", "client_secret", "auth_token"}
    return {key: value[key] for key in value if key.lower() not in forbidden}


def _google_event_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Translate the provider-neutral calendar command to Google event JSON."""
    start = str(body["start"])
    end = str(body["end"])
    timezone = str(body.get("timeZone") or "UTC")
    payload: dict[str, Any] = {
        "summary": str(body.get("summary") or "Buyer consultation"),
        "start": {"dateTime": start, "timeZone": timezone},
        "end": {"dateTime": end, "timeZone": timezone},
    }
    if body.get("locationId"):
        payload["location"] = str(body["locationId"])
    if body.get("journeyRef"):
        payload["description"] = json.dumps(
            {"buyerJourneyRef": body["journeyRef"]}, separators=(",", ":"), sort_keys=True
        )
    return payload


def _sendgrid_mail_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Translate a governed message command to SendGrid Mail Send JSON."""
    required = ("to", "from", "text")
    if any(not isinstance(body.get(key), str) or not body[key] for key in required):
        raise ProviderAdapterError(
            "payload_invalid", "SendGrid message requires to, from, and text", retryable=False
        )
    return {
        "personalizations": [{"to": [{"email": str(body["to"])}]}],
        "from": {"email": str(body["from"])},
        "subject": str(body.get("subject") or "Buyer operations message"),
        "content": [{"type": "text/plain", "value": str(body["text"])}],
    }


def _microsoft_event_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Translate the provider-neutral calendar command to Microsoft Graph JSON."""
    timezone = str(body.get("timeZone") or "UTC")
    payload: dict[str, Any] = {
        "subject": str(body.get("summary") or "Buyer consultation"),
        "start": {"dateTime": str(body["start"]), "timeZone": timezone},
        "end": {"dateTime": str(body["end"]), "timeZone": timezone},
    }
    if body.get("locationId"):
        payload["location"] = {"displayName": str(body["locationId"])}
    if body.get("journeyRef"):
        payload["body"] = {
            "contentType": "text",
            "content": "buyerJourneyRef=" + str(body["journeyRef"]),
        }
    return payload


def _docusign_envelope_payload(body: dict[str, Any]) -> dict[str, Any]:
    """Translate a governed agreement request to a template-backed DocuSign envelope."""
    template_id = body.get("templateId")
    if not isinstance(template_id, str) or not template_id:
        raise ProviderAdapterError(
            "configuration_incomplete",
            "DocuSign templateId is required for envelope creation",
            retryable=False,
        )
    payload: dict[str, Any] = {
        "status": "sent",
        "templateId": template_id,
        "emailSubject": str(body.get("emailSubject") or "Buyer representation agreement"),
        "customFields": {
            "textCustomFields": [
                {"name": "buyerOpsAgreementDigest", "value": str(body["agreementDigest"])}
            ]
        },
    }
    recipients = body.get("recipients")
    if recipients is not None:
        if not isinstance(recipients, list) or not all(
            isinstance(item, dict) for item in recipients
        ):
            raise ProviderAdapterError(
                "payload_invalid",
                "DocuSign recipients must be an array of objects",
                retryable=False,
            )
        roles: list[dict[str, str]] = []
        for item in recipients:
            if not all(
                isinstance(item.get(key), str) and item[key]
                for key in ("roleName", "name", "email")
            ):
                raise ProviderAdapterError(
                    "payload_invalid",
                    "DocuSign recipient requires roleName, name, and email",
                    retryable=False,
                )
            roles.append(
                {
                    "roleName": str(item["roleName"]),
                    "name": str(item["name"]),
                    "email": str(item["email"]),
                }
            )
        payload["templateRoles"] = roles
    return payload


def configured_direct_provider_adapters(raw: str | None = None) -> dict[str, DirectProviderAdapter]:
    encoded = (
        raw if raw is not None else os.environ.get("BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON", "")
    )
    if not encoded.strip():
        raise ValueError("BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON is required")
    try:
        values = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise ValueError("BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON must be valid JSON") from exc
    if not isinstance(values, list) or not values:
        raise ValueError("BUYER_OPS_DIRECT_PROVIDER_ADAPTERS_JSON must be a non-empty list")
    result: dict[str, DirectProviderAdapter] = {}
    for value in values:
        config = DirectProviderConfig.from_value(value)
        if config.connector_id in result:
            raise ValueError(f"duplicate direct provider connector: {config.connector_id}")
        result[config.connector_id] = DirectProviderAdapter(config)
    return result
