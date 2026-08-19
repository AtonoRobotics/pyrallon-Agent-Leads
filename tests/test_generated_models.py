from buyer_ops_contracts.generated.connector_gateway import Request as ConnectorRequest
from buyer_ops_contracts.generated.context import CompileRequest
from buyer_ops_contracts.generated.gateway import CognitiveProposal, CognitiveWorkRequest
from buyer_ops_contracts.generated.gateway_runtime import (
    CapabilityProfile,
    CredentialIdentity,
    GatewayFailure,
    RoutePolicy,
)
from buyer_ops_contracts.generated.habitat import HabitatEffectIntent
from buyer_ops_contracts.generated.ontology import WrittenBuyerAgreement
from buyer_ops_contracts.generated.operator_surface import JourneyView, OperatorPolicy
from buyer_ops_contracts.generated.ot01_ingress import AttributionInput
from buyer_ops_contracts.generated.release_activation import GateEvidence
from buyer_ops_contracts.generated.telemetry_slo import MetricObservation
from buyer_ops_contracts.generated.temporal import (
    ConnectorReconciliationInput,
    DomainChildInput,
    WorkerConfiguration,
    WorkflowInput,
)


def test_generated_gateway_models_parse_fixtures(load_fixture) -> None:
    CognitiveWorkRequest.model_validate(load_fixture("valid/cognitive_work_request.json"))
    CognitiveProposal.model_validate(load_fixture("valid/cognitive_proposal.json"))


def test_generated_ontology_model_parses_fixture(load_fixture) -> None:
    WrittenBuyerAgreement.model_validate(load_fixture("valid/written_buyer_agreement.json"))


def test_generated_habitat_model_parses_fixture(load_fixture) -> None:
    HabitatEffectIntent.model_validate(load_fixture("valid/effect_intent.json"))


def test_generated_temporal_model_parses_fixture(load_fixture) -> None:
    WorkflowInput.model_validate(load_fixture("valid/temporal_workflow_input.json"))
    ConnectorReconciliationInput.model_validate(
        load_fixture("valid/connector_reconciliation_input.json")
    )
    DomainChildInput.model_validate(load_fixture("valid/domain_child_input.json"))
    WorkerConfiguration.model_validate(load_fixture("valid/temporal_worker_configuration.json"))


def test_generated_context_model_parses_fixture(load_fixture) -> None:
    CompileRequest.model_validate(load_fixture("valid/context_compile_request.json"))


def test_generated_gateway_runtime_models_parse_fixtures(load_fixture) -> None:
    CredentialIdentity.model_validate(load_fixture("valid/gateway_credential_identity.json"))
    CapabilityProfile.model_validate(load_fixture("valid/gateway_capability_profile.json"))
    RoutePolicy.model_validate(load_fixture("valid/gateway_route_policy.json"))
    GatewayFailure.model_validate(load_fixture("valid/gateway_failure.json"))


def test_generated_closure_models_parse_fixtures(load_fixture) -> None:
    JourneyView.model_validate(load_fixture("closure/operator_surface_valid.json")["JourneyView"])
    OperatorPolicy.model_validate(
        load_fixture("closure/operator_surface_valid.json")["OperatorPolicy"]
    )
    MetricObservation.model_validate(
        load_fixture("closure/telemetry_slo_valid.json")["MetricObservation"]
    )
    AttributionInput.model_validate(
        load_fixture("closure/ot01_ingress_valid.json")["AttributionInput"]
    )
    ConnectorRequest.model_validate(load_fixture("closure/connector_gateway_valid.json")["Request"])
    GateEvidence.model_validate(
        load_fixture("closure/release_activation_valid.json")["GateEvidence"]
    )
