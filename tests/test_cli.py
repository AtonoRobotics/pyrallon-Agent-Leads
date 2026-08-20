from buyer_ops_contracts.cli import _admission_summary


def test_admission_summary_supports_published_operator_policy_shape() -> None:
    assert _admission_summary(
        {
            "message_type": "operator_policy",
            "policy_id": "policy-1",
            "record_version": 1,
        }
    ) == {
        "id": "policy-1",
        "recordType": "operator_policy",
        "version": 1,
    }


def test_admission_summary_preserves_canonical_shape() -> None:
    assert _admission_summary({"recordType": "Person", "id": "person-1", "version": 2}) == {
        "id": "person-1",
        "recordType": "Person",
        "version": 2,
    }
