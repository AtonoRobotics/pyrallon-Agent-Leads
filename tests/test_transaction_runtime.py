from buyer_ops_contracts.transaction_runtime import TransactionRuntime, TransactionRuntimeError


def _canonical(record_type: str, record_id: str, **extra: object) -> dict:
    now = "2026-01-01T00:00:00Z"
    return {
        "id": record_id,
        "tenantId": "tenant-1",
        "schemaVersion": "buyer-ops/0.3.0",
        "recordType": record_type,
        "version": 1,
        "createdAt": now,
        "updatedAt": now,
        "effectiveFrom": now,
        "createdBy": {"actorType": "license_holder", "actorId": "agent-1"},
        "sourceEvidenceIds": ["contract-evidence-1"],
        "status": "active",
        **extra,
    }


def _transaction() -> dict:
    return _canonical(
        "Transaction",
        "transaction-1",
        journeyId="journey-1",
        buyingPartyId="party-1",
        brokerageId="brokerage-1",
        propertyReferenceId="property-1",
        partyIds=["party-1", "brokerage-1"],
        executedArtifactId="artifact-contract-1",
        executedArtifactDigest="sha256:" + "a" * 64,
        initiatedAt="2026-01-01T00:00:00Z",
        transactionState="under_contract",
    )


def _date() -> dict:
    return _canonical(
        "ConfirmedTransactionDate",
        "date-1",
        transactionId="transaction-1",
        dateType="closing",
        date="2026-02-01T17:00:00Z",
        confirmationSourceType="DocumentArtifact",
        confirmationSourceId="artifact-contract-1",
        confirmationSourceDigest="sha256:" + "a" * 64,
        confirmedAt="2026-01-01T00:00:00Z",
        confirmationState="confirmed",
    )


def _milestone() -> dict:
    return _canonical(
        "TransactionMilestone",
        "milestone-1",
        transactionId="transaction-1",
        milestoneType="closing",
        dueAt="2026-02-01T17:00:00Z",
        confirmationState="confirmed",
        confirmationEvidenceId="contract-evidence-1",
        milestoneState="pending",
    )


def test_plan_requires_executed_artifact_and_agent_confirmed_date() -> None:
    plan = TransactionRuntime().build_plan(
        transaction=_transaction(), milestones=[_milestone()], confirmed_dates=[_date()]
    )
    assert plan["legalInterpretation"] is False
    action = TransactionRuntime().authorize_deadline_action(
        plan=plan,
        milestone_id="milestone-1",
        action_evidence_id="action-evidence-1",
        actor_type="license_holder",
    )
    assert action["dueAt"] == "2026-02-01T17:00:00Z"


def test_unconfirmed_date_cannot_authorize_plan() -> None:
    date = _date()
    date["confirmationState"] = "proposed"
    try:
        TransactionRuntime().build_plan(
            transaction=_transaction(), milestones=[_milestone()], confirmed_dates=[date]
        )
    except TransactionRuntimeError as exc:
        assert str(exc) == "transaction_date_not_agent_confirmed"
    else:
        raise AssertionError("unconfirmed transaction date was accepted")


def test_mismatched_milestone_date_is_rejected() -> None:
    milestone = _milestone()
    milestone["dueAt"] = "2026-02-02T17:00:00Z"
    try:
        TransactionRuntime().build_plan(
            transaction=_transaction(), milestones=[milestone], confirmed_dates=[_date()]
        )
    except TransactionRuntimeError as exc:
        assert str(exc) == "transaction_milestone_date_conflict"
    else:
        raise AssertionError("mismatched transaction date was accepted")
