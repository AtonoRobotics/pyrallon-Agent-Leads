from buyer_ops_contracts.generated.gateway import CognitiveProposal, CognitiveWorkRequest
from buyer_ops_contracts.generated.ontology import WrittenBuyerAgreement


def test_generated_gateway_models_parse_fixtures(load_fixture) -> None:
    CognitiveWorkRequest.model_validate(load_fixture("valid/cognitive_work_request.json"))
    CognitiveProposal.model_validate(load_fixture("valid/cognitive_proposal.json"))


def test_generated_ontology_model_parses_fixture(load_fixture) -> None:
    WrittenBuyerAgreement.model_validate(load_fixture("valid/written_buyer_agreement.json"))

