"""Tenant-scoped persistence for the OT01 1.1 acknowledgment contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, cast

import rfc8785
from psycopg.types.json import Jsonb

from .acknowledgment import (
    build_acknowledgment_decision,
    configuration_incomplete_decision,
    normalize_opt_out_text,
    validate_acknowledgment_config,
)
from .canonical_repository import CanonicalRepository, Connection, Cursor, VersionConflict
from .structural import validate_record


class AcknowledgmentRepository:
    def __init__(self, connection: Connection, *, tenant_id: str) -> None:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        self._connection, self._tenant_id = connection, tenant_id

    @staticmethod
    def _identity(config: dict[str, Any]) -> tuple[str, str]:
        kind = str(config["messageType"])
        return kind, str(
            config["policyId"] if kind == "acknowledgment_policy" else config["lexiconId"]
        )

    def admit_config(
        self, config: dict[str, Any], *, expected_version: int | None = None
    ) -> dict[str, Any]:
        validate_acknowledgment_config(config)
        if config["tenantId"] != self._tenant_id:
            raise ValueError("acknowledgment configuration tenant mismatch")
        kind, config_id = self._identity(config)
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT record_version,status FROM ingress_ack_configs_current WHERE tenant_id=%s AND config_type=%s AND config_id=%s FOR UPDATE",
                    (self._tenant_id, kind, config_id),
                )
                row = cursor.fetchone()
                current = None if row is None else int(cast(int, row[0]))
                version = int(config["recordVersion"])
                if (current is None and (expected_version is not None or version != 1)) or (
                    current is not None and (expected_version != current or version != current + 1)
                ):
                    raise VersionConflict("acknowledgment configuration version conflict")
                if row is None and config["status"] not in {"draft", "active"}:
                    raise ValueError("initial acknowledgment configuration must be draft or active")
                allowed = {
                    "draft": {"draft", "active", "retired"},
                    "active": {"active", "superseded", "retired"},
                    "superseded": set(),
                    "retired": set(),
                }
                if row is not None and config["status"] not in allowed[str(row[1])]:
                    raise ValueError("acknowledgment configuration lifecycle transition is invalid")
                cursor.execute(
                    "INSERT INTO ingress_ack_config_versions (tenant_id,config_type,config_id,record_version,config) VALUES (%s,%s,%s,%s,%s)",
                    (self._tenant_id, kind, config_id, version, Jsonb(config)),
                )
                cursor.execute(
                    "INSERT INTO ingress_ack_configs_current (tenant_id,config_type,config_id,record_version,status,config) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,config_type,config_id) DO UPDATE SET record_version=EXCLUDED.record_version,status=EXCLUDED.status,config=EXCLUDED.config,updated_at=clock_timestamp()",
                    (self._tenant_id, kind, config_id, version, config["status"], Jsonb(config)),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return config

    def decide(
        self, request: dict[str, Any], raw_inbound_text: str, template_bytes: bytes
    ) -> dict[str, Any]:
        validate_record(request, "ot01_ingress")
        if request["tenantId"] != self._tenant_id:
            raise ValueError("acknowledgment request tenant mismatch")
        request_digest = f"sha256:{hashlib.sha256(rfc8785.dumps([request, normalize_opt_out_text(raw_inbound_text)])).hexdigest()}"
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT request_digest,decision FROM ingress_acknowledgment_decisions WHERE tenant_id=%s AND idempotency_key=%s FOR SHARE",
                    (self._tenant_id, request["idempotencyKey"]),
                )
                replay = cursor.fetchone()
                if replay is not None:
                    if replay[0] != request_digest:
                        raise ValueError(
                            "idempotency key reused with different acknowledgment input"
                        )
                    result = replay[1]
                else:
                    self._require_external_identity(cursor, request["externalMessageIdentityRef"])
                    policy = self._load_exact(
                        cursor,
                        request["acknowledgmentPolicyRef"],
                        "acknowledgment_policy",
                        evaluated_at=request["requestedAt"],
                    )
                    lexicon = self._load_exact(
                        cursor,
                        request["optOutLexiconRef"],
                        "opt_out_lexicon",
                        evaluated_at=request["requestedAt"],
                    )
                    result = (
                        configuration_incomplete_decision(request)
                        if policy is None or lexicon is None
                        else build_acknowledgment_decision(
                            request, policy, lexicon, raw_inbound_text, template_bytes
                        )
                    )
                    if result["optOutMatched"]:
                        CanonicalRepository(self._connection, tenant_id=self._tenant_id).save_on(
                            cursor, request["suppressionRecordCandidate"]
                        )
                    cursor.execute(
                        "INSERT INTO ingress_acknowledgment_decisions (tenant_id,decision_id,request_id,idempotency_key,request_digest,decision) VALUES (%s,%s,%s,%s,%s,%s)",
                        (
                            self._tenant_id,
                            request["decisionId"],
                            request["requestId"],
                            request["idempotencyKey"],
                            request_digest,
                            Jsonb(result),
                        ),
                    )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return cast(dict[str, Any], result)

    def admit_outcome(self, outcome: dict[str, Any]) -> dict[str, Any]:
        validate_record(outcome, "ot01_ingress")
        if (
            outcome["messageType"] != "acknowledgment_outcome"
            or outcome["tenantId"] != self._tenant_id
        ):
            raise ValueError("invalid acknowledgment outcome scope")
        try:
            with self._connection.cursor() as cursor:
                self._set_tenant(cursor)
                cursor.execute(
                    "SELECT decision FROM ingress_acknowledgment_decisions WHERE tenant_id=%s AND decision_id=%s FOR SHARE",
                    (self._tenant_id, outcome["decisionId"]),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ValueError("acknowledgment outcome references an unavailable decision")
                decision_value = row[0]
                decision = cast(
                    dict[str, Any],
                    json.loads(decision_value)
                    if isinstance(decision_value, str)
                    else decision_value,
                )
                if (
                    outcome["captureEventId"] != decision["externalMessageIdentityRef"]["recordId"]
                    or outcome["captureCommittedAt"] != decision["capturedAt"]
                ):
                    raise ValueError("acknowledgment outcome capture boundary mismatch")
                captured = datetime.fromisoformat(
                    outcome["captureCommittedAt"].replace("Z", "+00:00")
                )
                observed = datetime.fromisoformat(outcome["observedAt"].replace("Z", "+00:00"))
                if observed < captured:
                    raise ValueError("acknowledgment outcome cannot precede capture")
                if outcome["state"] == "failed" and "failureCode" not in outcome:
                    raise ValueError("failed acknowledgment outcome requires failureCode")
                if outcome["state"] == "suppressed" and not decision["optOutMatched"]:
                    raise ValueError("suppressed outcome requires an opt-out decision")
                if (
                    outcome["state"] == "no_ack_required"
                    and decision["disposition"] != "no_ack_required"
                ):
                    raise ValueError("no-ack outcome does not match its decision")
                cursor.execute(
                    "INSERT INTO ingress_acknowledgment_outcomes (tenant_id,outcome_id,decision_id,outcome) VALUES (%s,%s,%s,%s)",
                    (self._tenant_id, outcome["outcomeId"], outcome["decisionId"], Jsonb(outcome)),
                )
        except Exception:
            self._connection.rollback()
            raise
        self._connection.commit()
        return outcome

    def _load_exact(
        self,
        cursor: Cursor,
        reference: dict[str, Any],
        kind: str,
        *,
        evaluated_at: str,
    ) -> dict[str, Any] | None:
        cursor.execute(
            "SELECT record_version,status,config FROM ingress_ack_configs_current WHERE tenant_id=%s AND config_type=%s AND config_id=%s FOR SHARE",
            (self._tenant_id, kind, reference["recordId"]),
        )
        row = cursor.fetchone()
        expected_type = (
            "AcknowledgmentPolicy" if kind == "acknowledgment_policy" else "OptOutLexicon"
        )
        if (
            row is None
            or reference
            != {
                "recordId": reference["recordId"],
                "recordType": expected_type,
                "version": row[0],
                "status": row[1],
            }
            or row[1] != "active"
        ):
            return None
        value = row[2]
        config = cast(dict[str, Any], json.loads(value) if isinstance(value, str) else value)
        evaluated = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00")).astimezone(UTC)
        starts = datetime.fromisoformat(config["effectiveFrom"].replace("Z", "+00:00")).astimezone(
            UTC
        )
        ends = (
            None
            if "effectiveTo" not in config
            else datetime.fromisoformat(config["effectiveTo"].replace("Z", "+00:00")).astimezone(
                UTC
            )
        )
        return config if starts <= evaluated and (ends is None or evaluated < ends) else None

    def _require_external_identity(self, cursor: Cursor, reference: dict[str, Any]) -> None:
        if (
            reference.get("recordType") != "ExternalMessageIdentity"
            or reference.get("status") != "current"
        ):
            raise ValueError("external message identity reference type or status is invalid")
        cursor.execute(
            "SELECT 1 FROM closure_records_current WHERE tenant_id=%s AND record_type='ExternalMessageIdentity' AND record_id=%s AND record_version=%s FOR SHARE",
            (self._tenant_id, reference["recordId"], reference["version"]),
        )
        if cursor.fetchone() is None:
            raise ValueError("external message identity is not the exact current record")

    def _set_tenant(self, cursor: Cursor) -> None:
        cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (self._tenant_id,))
