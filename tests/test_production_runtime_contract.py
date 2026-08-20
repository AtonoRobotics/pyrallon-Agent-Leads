from buyer_ops_contracts import ContractViolation, validate_record


def _ref(record_id: str, record_type: str) -> dict[str, object]:
    return {
        "recordId": record_id,
        "recordType": record_type,
        "recordVersion": 1,
        "digest": "sha256:" + "a" * 64,
    }


def _base(record_type: str) -> dict[str, object]:
    return {
        "schemaVersion": "production-runtime/1.0.0",
        "recordId": record_type.lower() + "-1",
        "recordVersion": 1,
        "tenantId": "tenant-1",
        "effectiveFrom": "2030-01-01T00:00:00Z",
        "status": "current",
        "evidenceRefs": [_ref("evidence-1", "Evidence")],
        "recordType": record_type,
    }


def test_journey_state_derivation_is_explicit_and_versioned() -> None:
    record = _base("JourneyStateDerivation")
    record.update(
        {
            "derivationVersion": "journey-state/1.0.0",
            "sourceRecordTypes": ["BuyerJourney", "Message", "QualificationObservation"],
            "stateFields": ["ingress_state", "qualification_state", "consultation_state"],
            "precedence": [
                {
                    "stateField": "qualification_state",
                    "orderedRules": ["contradicted", "stale", "sufficient_for_consult"],
                }
            ],
            "unknownState": "unknown",
            "outputSchemaVersion": "ot01-journey-state/1.0.0",
        }
    )
    validate_record(record, "production_runtime")


def test_operator_workflow_binding_requires_atomic_command_result() -> None:
    record = _base("OperatorWorkflowCommandBinding")
    record.update(
        {
            "commandType": "pause_workflow",
            "targetRecordTypes": ["WorkflowReference"],
            "temporalSignal": "BuyerJourneyWorkflow.pause",
            "idempotencyScope": "workflow_command",
            "expectedVersionRequired": True,
            "resultRecordType": "CommandResult",
            "canonicalMutationAtomicity": "command_result_and_workflow_reference_same_transaction",
        }
    )
    validate_record(record, "production_runtime")


def test_capability_binding_requires_activation_and_inventory_refs() -> None:
    record = _base("CapabilityActivationBinding")
    record.update(
        {
            "connectorId": "google-calendar",
            "capabilityId": "calendar.create_event",
            "activationRecordRef": _ref("activation-1", "ReleaseActivation"),
            "inventoryRecordRef": _ref("inventory-1", "CapabilityInventory"),
            "actionClasses": ["consultation_booking"],
            "channel": "calendar",
            "authorityScopes": ["calendar.write"],
            "revalidationRequired": True,
        }
    )
    validate_record(record, "production_runtime")


def test_telemetry_binding_binds_ratio_event_sets() -> None:
    record = _base("TelemetryEventBinding")
    record.update(
        {
            "metricId": "inbound_ack_latency",
            "startEventType": "inbound.captured",
            "endEventType": "acknowledgment.confirmed",
            "correlationKey": "external_message_identity",
            "numeratorEventSetDigest": "sha256:" + "b" * 64,
            "denominatorEventSetDigest": "sha256:" + "c" * 64,
            "dimensions": ["tenant_id", "channel"],
            "window": "rolling_24h",
            "thresholds": {"p95_ms": 120000},
            "ownerId": "operations",
            "retentionClass": "operational_90d",
        }
    )
    validate_record(record, "production_runtime")


def test_accessibility_binding_requires_two_sided_evidence() -> None:
    record = _base("AccessibilityBinding")
    record.update(
        {
            "operatorAcceptanceRef": _ref("operator-a11y-1", "AccessibilityAcceptance"),
            "closureEvidenceRef": _ref("closure-a11y-1", "AccessibilityEvidence"),
            "surface": "web_and_ios",
            "buildDigest": "sha256:" + "d" * 64,
            "expiresAt": "2030-02-01T00:00:00Z",
            "bindingDigest": "sha256:" + "e" * 64,
        }
    )
    validate_record(record, "production_runtime")


def test_worker_configuration_cannot_use_empty_inventory() -> None:
    record = _base("WorkerConfiguration")
    record.update(
        {
            "workerId": "worker-1",
            "taskQueue": "buyer-ops-production",
            "workflowTypes": ["BuyerJourneyWorkflow"],
            "activityTypes": ["reconcile_journey_state"],
            "maxConcurrentWorkflowTasks": 10,
            "maxConcurrentActivities": 20,
            "maxCachedWorkflows": 100,
            "gracefulShutdownSeconds": 30,
            "deploymentDigest": "sha256:" + "f" * 64,
        }
    )
    validate_record(record, "production_runtime")


def test_production_runtime_rejects_unknown_record_type() -> None:
    record = _base("UnknownRuntimeRecord")
    try:
        validate_record(record, "production_runtime")
    except ContractViolation:
        return
    raise AssertionError("unknown production runtime record was admitted")
