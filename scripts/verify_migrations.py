from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "0001_canonical_records.sql"
ROLLBACK = ROOT / "migrations" / "0001_canonical_records.rollback.sql"
EVIDENCE_MIGRATION = ROOT / "migrations" / "0002_evidence_ledger.sql"
EVIDENCE_ROLLBACK = ROOT / "migrations" / "0002_evidence_ledger.rollback.sql"
IDENTITY_MIGRATION = ROOT / "migrations" / "0003_identity_resolution.sql"
IDENTITY_ROLLBACK = ROOT / "migrations" / "0003_identity_resolution.rollback.sql"
ONTOLOGY_02_MIGRATION = ROOT / "migrations" / "0004_ontology_0_2.sql"
ONTOLOGY_02_ROLLBACK = ROOT / "migrations" / "0004_ontology_0_2.rollback.sql"
ONTOLOGY_02_REPAIR = ROOT / "migrations" / "ONTOLOGY-0.2-FORWARD-REPAIR.md"
HABITAT_MIGRATION = ROOT / "migrations" / "0005_habitat_permits.sql"
HABITAT_ROLLBACK = ROOT / "migrations" / "0005_habitat_permits.rollback.sql"
ONTOLOGY_03_MIGRATION = ROOT / "migrations" / "0006_ontology_0_3.sql"
ONTOLOGY_03_ROLLBACK = ROOT / "migrations" / "0006_ontology_0_3.rollback.sql"
INGRESS_MIGRATION = ROOT / "migrations" / "0007_inbound_events.sql"
INGRESS_ROLLBACK = ROOT / "migrations" / "0007_inbound_events.rollback.sql"
OPERATOR_MIGRATION = ROOT / "migrations" / "0008_operator_commands.sql"
OPERATOR_ROLLBACK = ROOT / "migrations" / "0008_operator_commands.rollback.sql"


REQUIRED_FRAGMENTS = (
    "CREATE TABLE canonical_records_current",
    "CREATE TABLE canonical_record_versions",
    "PRIMARY KEY (tenant_id, record_id)",
    "PRIMARY KEY (tenant_id, record_id, version)",
    "FOREIGN KEY (tenant_id, record_id)",
    "ALTER TABLE canonical_records_current ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE canonical_record_versions ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE canonical_records_current FORCE ROW LEVEL SECURITY",
    "ALTER TABLE canonical_record_versions FORCE ROW LEVEL SECURITY",
    "USING (tenant_id = current_setting('app.tenant_id', true))",
    "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
    "CREATE TRIGGER canonical_record_versions_append_only",
    "CHECK ((record->>'tenantId') IS NOT DISTINCT FROM tenant_id)",
)


def main() -> None:
    if not MIGRATION.is_file():
        raise SystemExit(f"missing required migration: {MIGRATION}")
    sql = MIGRATION.read_text()
    missing = [fragment for fragment in REQUIRED_FRAGMENTS if fragment not in sql]
    if missing:
        raise SystemExit(f"migration integrity failure; missing: {missing}")
    if "DROP TABLE" in sql.upper():
        raise SystemExit("forward migration must not drop canonical tables")
    if not ROLLBACK.is_file():
        raise SystemExit(f"missing required rollback migration: {ROLLBACK}")
    rollback = ROLLBACK.read_text()
    if (
        "rollback is prohibited" not in rollback
        or "DROP TABLE canonical_records_current" not in rollback
    ):
        raise SystemExit("rollback must refuse canonical data loss before removing empty tables")
    if not EVIDENCE_MIGRATION.is_file() or not EVIDENCE_ROLLBACK.is_file():
        raise SystemExit("missing evidence ledger forward or rollback migration")
    evidence_sql = EVIDENCE_MIGRATION.read_text()
    evidence_required = (
        "CREATE TABLE evidence_artifact_versions",
        "CREATE TABLE evidence_ledger",
        "CREATE TABLE evidence_checkpoints",
        "CREATE TABLE projection_fences",
        "CREATE TABLE evidence_deletion_tombstones",
        "CREATE TABLE derived_invalidation_events",
        "CREATE TRIGGER evidence_ledger_append_only",
        "FORCE ROW LEVEL SECURITY",
    )
    evidence_missing = [item for item in evidence_required if item not in evidence_sql]
    if evidence_missing:
        raise SystemExit(f"evidence migration integrity failure; missing: {evidence_missing}")
    if not IDENTITY_MIGRATION.is_file() or not IDENTITY_ROLLBACK.is_file():
        raise SystemExit("missing identity mapping forward or rollback migration")
    identity_sql = IDENTITY_MIGRATION.read_text()
    identity_required = (
        "CREATE TABLE external_identity_mappings_current",
        "CREATE TABLE external_identity_mapping_versions",
        "CREATE TABLE identity_resolution_cases",
        "resolution_basis",
        "FORCE ROW LEVEL SECURITY",
    )
    identity_missing = [item for item in identity_required if item not in identity_sql]
    if identity_missing:
        raise SystemExit(f"identity migration integrity failure; missing: {identity_missing}")
    if (
        not ONTOLOGY_02_MIGRATION.is_file()
        or not ONTOLOGY_02_ROLLBACK.is_file()
        or not ONTOLOGY_02_REPAIR.is_file()
    ):
        raise SystemExit(
            "missing ontology 0.2 forward, rollback refusal, or forward-repair artifact"
        )
    ontology_02_sql = ONTOLOGY_02_MIGRATION.read_text()
    ontology_02_required = (
        "buyer-ops/0.2.0",
        "EpistemicItem forward repair",
        "decisionAuthorityState",
        "territory",
        "canonical_record_versions_append_only",
    )
    ontology_02_missing = [item for item in ontology_02_required if item not in ontology_02_sql]
    if ontology_02_missing:
        raise SystemExit(
            f"ontology 0.2 migration integrity failure; missing: {ontology_02_missing}"
        )
    if "rollback is prohibited" not in ONTOLOGY_02_ROLLBACK.read_text():
        raise SystemExit("ontology 0.2 rollback must refuse semantic data loss")
    if not HABITAT_MIGRATION.is_file() or not HABITAT_ROLLBACK.is_file():
        raise SystemExit("missing Habitat permit forward or rollback-refusal migration")
    habitat_sql = HABITAT_MIGRATION.read_text()
    habitat_required = (
        "CREATE TABLE habitat_authority_decisions",
        "CREATE TABLE habitat_effect_permits",
        "UNIQUE (tenant_id, intent_id)",
        "UNIQUE (tenant_id, idempotency_key)",
        "habitat_authority_decisions_append_only",
        "FORCE ROW LEVEL SECURITY",
    )
    habitat_missing = [item for item in habitat_required if item not in habitat_sql]
    if habitat_missing:
        raise SystemExit(f"Habitat migration integrity failure; missing: {habitat_missing}")
    if "rollback refused" not in HABITAT_ROLLBACK.read_text():
        raise SystemExit("Habitat rollback must refuse authority-evidence loss")
    if not ONTOLOGY_03_MIGRATION.is_file() or not ONTOLOGY_03_ROLLBACK.is_file():
        raise SystemExit("missing ontology 0.3 migration or rollback")
    ontology_03 = ONTOLOGY_03_MIGRATION.read_text()
    for fragment in (
        "buyer-ops/0.3.0",
        "canonical_records_current",
        "canonical_record_versions",
        "jsonb_set",
    ):
        if fragment not in ontology_03:
            raise SystemExit(f"ontology 0.3 migration missing: {fragment}")
    if "rollback refused" not in ONTOLOGY_03_ROLLBACK.read_text():
        raise SystemExit("ontology 0.3 rollback must refuse incompatible canonical data")
    if not INGRESS_MIGRATION.is_file() or not INGRESS_ROLLBACK.is_file():
        raise SystemExit("missing inbound event forward or rollback-refusal migration")
    ingress_sql = INGRESS_MIGRATION.read_text()
    for fragment in (
        "CREATE TABLE inbound_events",
        "UNIQUE (tenant_id, provider_account_ref, provider_event_id)",
        "inbound_events_append_only",
        "FORCE ROW LEVEL SECURITY",
    ):
        if fragment not in ingress_sql:
            raise SystemExit(f"inbound event migration missing: {fragment}")
    if "rollback refused" not in INGRESS_ROLLBACK.read_text():
        raise SystemExit("inbound event rollback must refuse admitted-event loss")
    if not OPERATOR_MIGRATION.is_file() or not OPERATOR_ROLLBACK.is_file():
        raise SystemExit("missing operator command forward or rollback-refusal migration")
    operator_sql = OPERATOR_MIGRATION.read_text()
    for fragment in (
        "CREATE TABLE operator_command_results",
        "PRIMARY KEY (tenant_id, idempotency_key)",
        "operator_command_results_append_only",
        "FORCE ROW LEVEL SECURITY",
    ):
        if fragment not in operator_sql:
            raise SystemExit(f"operator command migration missing: {fragment}")
    if "rollback refused" not in OPERATOR_ROLLBACK.read_text():
        raise SystemExit("operator command rollback must refuse applied-result loss")
    control = ROOT / "migrations" / "0009_control_plane.sql"
    control_rollback = ROOT / "migrations" / "0009_control_plane.rollback.sql"
    if not control.is_file() or not control_rollback.is_file():
        raise SystemExit("missing control-plane forward or rollback-refusal migration")
    control_sql = control.read_text()
    for fragment in (
        "inbound_events_stable_message_key",
        "CREATE TABLE IF NOT EXISTS closure_records",
        "CREATE TABLE IF NOT EXISTS closure_records_current",
        "CREATE TABLE IF NOT EXISTS release_gate_evidence",
        "CREATE TABLE IF NOT EXISTS release_activation_decisions",
        "CREATE TABLE IF NOT EXISTS telemetry_observations",
        "CREATE TABLE IF NOT EXISTS ingress_attribution",
        "CREATE TABLE IF NOT EXISTS operator_actor_tenancies",
        "FORCE ROW LEVEL SECURITY",
    ):
        if fragment not in control_sql:
            raise SystemExit(f"control plane migration missing: {fragment}")
    if "rollback refused" not in control_rollback.read_text():
        raise SystemExit("control plane rollback must refuse activation evidence loss")
    identity = ROOT / "migrations" / "0010_external_message_identity.sql"
    identity_rollback = ROOT / "migrations" / "0010_external_message_identity.rollback.sql"
    if not identity.is_file() or not identity_rollback.is_file():
        raise SystemExit("missing OPEN-019 external-message-identity migration")
    identity_sql = identity.read_text()
    for fragment in (
        "inbound_events_external_message_uidx",
        "CREATE TABLE IF NOT EXISTS inbound_message_conflicts",
        "external_message_id",
    ):
        if fragment not in identity_sql:
            raise SystemExit(f"OPEN-019 migration missing: {fragment}")
    if "rollback refused" not in identity_rollback.read_text():
        raise SystemExit("OPEN-019 rollback must refuse identity-evidence loss")
    operator_11 = ROOT / "migrations" / "0011_operator_surface_1_1.sql"
    operator_11_rollback = ROOT / "migrations" / "0011_operator_surface_1_1.rollback.sql"
    if not operator_11.is_file() or not operator_11_rollback.is_file():
        raise SystemExit("missing Operator Surface 1.1 migration or rollback refusal")
    operator_11_sql = operator_11.read_text()
    for fragment in (
        "CREATE TABLE operator_policy_versions",
        "CREATE TABLE operator_policies_current",
        "operator_policy_versions_append_only",
        "ADD COLUMN authorization_id",
        "FROM canonical_records_current",
        "operator-surface/1.1.0",
        "FORCE ROW LEVEL SECURITY",
    ):
        if fragment not in operator_11_sql:
            raise SystemExit(f"Operator Surface 1.1 migration missing: {fragment}")
    if "rollback refused" not in operator_11_rollback.read_text():
        raise SystemExit("Operator Surface 1.1 rollback must refuse policy/evidence loss")
    acknowledgment = ROOT / "migrations" / "0012_ot01_acknowledgment.sql"
    acknowledgment_rollback = ROOT / "migrations" / "0012_ot01_acknowledgment.rollback.sql"
    if not acknowledgment.is_file() or not acknowledgment_rollback.is_file():
        raise SystemExit("missing OT01 acknowledgment forward migration or rollback refusal")
    acknowledgment_sql = acknowledgment.read_text()
    for fragment in (
        "CREATE TABLE ingress_ack_config_versions",
        "CREATE TABLE ingress_ack_configs_current",
        "CREATE TABLE ingress_acknowledgment_decisions",
        "CREATE TABLE ingress_acknowledgment_outcomes",
        "ingress_acknowledgment_outcome_event_uidx",
        "ingress_acknowledgment_decisions_append_only",
        "ot01-ingress/1.1.0",
        "FORCE ROW LEVEL SECURITY",
    ):
        if fragment not in acknowledgment_sql:
            raise SystemExit(f"OT01 acknowledgment migration missing: {fragment}")
    if "rollback refused" not in acknowledgment_rollback.read_text():
        raise SystemExit("OT01 acknowledgment rollback must refuse admitted evidence loss")
    open025 = ROOT / "migrations" / "0013_actor_tenant_authorization.sql"
    open025_rollback = ROOT / "migrations" / "0013_actor_tenant_authorization.rollback.sql"
    if not open025.is_file() or not open025_rollback.is_file():
        raise SystemExit("missing OPEN-025 ActorTenantAuthorization migration or rollback refusal")
    open025_sql = open025.read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS actor_tenant_authorization_versions",
        "CREATE TABLE IF NOT EXISTS actor_tenant_authorizations_current",
        "actor_tenant_authorization_versions_append_only",
        "app.actor_id",
        "FORCE ROW LEVEL SECURITY",
    ):
        if fragment not in open025_sql:
            raise SystemExit(f"OPEN-025 migration missing: {fragment}")
    if "rollback refused" not in open025_rollback.read_text():
        raise SystemExit("OPEN-025 rollback must refuse authorization evidence loss")
    open026 = ROOT / "migrations" / "0014_release_activation.sql"
    open026_rollback = ROOT / "migrations" / "0014_release_activation.rollback.sql"
    if not open026.is_file() or not open026_rollback.is_file():
        raise SystemExit("missing OPEN-026 ReleaseActivation migration or rollback refusal")
    open026_sql = open026.read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS release_activation_versions",
        "release_activation_versions_append_only",
        "FORCE ROW LEVEL SECURITY",
        "payload jsonb NOT NULL",
    ):
        if fragment not in open026_sql:
            raise SystemExit(f"OPEN-026 migration missing: {fragment}")
    if "rollback refused" not in open026_rollback.read_text():
        raise SystemExit("OPEN-026 rollback must refuse activation evidence loss")
    activation_concurrency = ROOT / "migrations" / "0015_release_activation_concurrency.sql"
    activation_concurrency_rollback = (
        ROOT / "migrations" / "0015_release_activation_concurrency.rollback.sql"
    )
    if not activation_concurrency.is_file() or not activation_concurrency_rollback.is_file():
        raise SystemExit("missing Release Activation 1.1 concurrency migration or rollback refusal")
    activation_concurrency_sql = activation_concurrency.read_text()
    for fragment in (
        "activation_version integer",
        "release_activation_decisions_version_uidx",
        "release_activation_expected_version_matches",
        "expectedActivationVersion",
    ):
        if fragment not in activation_concurrency_sql:
            raise SystemExit(f"Release Activation concurrency migration missing: {fragment}")
    if "rollback refused" not in activation_concurrency_rollback.read_text():
        raise SystemExit("Release Activation concurrency rollback must refuse decision loss")
    credentials = ROOT / "migrations" / "0016_connector_credentials.sql"
    credentials_rollback = ROOT / "migrations" / "0016_connector_credentials.rollback.sql"
    if not credentials.is_file() or not credentials_rollback.is_file():
        raise SystemExit("missing connector credential forward migration or rollback refusal")
    credentials_sql = credentials.read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS connector_oauth_sessions",
        "CREATE TABLE IF NOT EXISTS connector_credentials",
        "FORCE ROW LEVEL SECURITY",
        "ciphertext bytea NOT NULL",
        "code_verifier text NOT NULL",
    ):
        if fragment not in credentials_sql:
            raise SystemExit(f"connector credential migration missing: {fragment}")
    if "rollback refused" not in credentials_rollback.read_text():
        raise SystemExit("connector credential rollback must refuse credential evidence loss")
    platform_oauth = ROOT / "migrations" / "0017_platform_oauth_clients.sql"
    platform_oauth_rollback = ROOT / "migrations" / "0017_platform_oauth_clients.rollback.sql"
    if not platform_oauth.is_file() or not platform_oauth_rollback.is_file():
        raise SystemExit("missing platform OAuth client migration or rollback refusal")
    platform_sql = platform_oauth.read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS platform_oauth_clients",
        "issuer text PRIMARY KEY",
        "ciphertext bytea NOT NULL",
    ):
        if fragment not in platform_sql:
            raise SystemExit(f"platform OAuth client migration missing: {fragment}")
    if "rollback refused" not in platform_oauth_rollback.read_text():
        raise SystemExit("platform OAuth client rollback must refuse client evidence loss")
    twilio_oauth = ROOT / "migrations" / "0018_platform_oauth_twilio.sql"
    twilio_oauth_rollback = ROOT / "migrations" / "0018_platform_oauth_twilio.rollback.sql"
    if not twilio_oauth.is_file() or not twilio_oauth_rollback.is_file():
        raise SystemExit("missing Twilio platform OAuth migration or rollback refusal")
    for fragment in ("platform_oauth_clients_issuer_check", "'twilio'"):
        if fragment not in twilio_oauth.read_text():
            raise SystemExit(f"Twilio platform OAuth migration missing: {fragment}")
    if "rollback refused" not in twilio_oauth_rollback.read_text():
        raise SystemExit("Twilio platform OAuth rollback must refuse client evidence loss")
    cognitive_credentials = ROOT / "migrations" / "0019_cognitive_credentials.sql"
    cognitive_credentials_rollback = ROOT / "migrations" / "0019_cognitive_credentials.rollback.sql"
    if not cognitive_credentials.is_file() or not cognitive_credentials_rollback.is_file():
        raise SystemExit("missing cognitive credential migration or rollback refusal")
    for fragment in (
        "CREATE TABLE IF NOT EXISTS cognitive_oauth_sessions",
        "CREATE TABLE IF NOT EXISTS cognitive_credentials",
        "FORCE ROW LEVEL SECURITY",
        "ciphertext bytea NOT NULL",
    ):
        if fragment not in cognitive_credentials.read_text():
            raise SystemExit(f"cognitive credential migration missing: {fragment}")
    if "rollback refused" not in cognitive_credentials_rollback.read_text():
        raise SystemExit("cognitive credential rollback must refuse credential evidence loss")
    oauth_return = ROOT / "migrations" / "0020_oauth_return_origin.sql"
    oauth_return_rollback = ROOT / "migrations" / "0020_oauth_return_origin.rollback.sql"
    if not oauth_return.is_file() or not oauth_return_rollback.is_file():
        raise SystemExit("missing OAuth return-origin migration or rollback")
    if "ADD COLUMN IF NOT EXISTS return_origin text" not in oauth_return.read_text():
        raise SystemExit("OAuth return-origin migration missing return_origin")
    if "DROP COLUMN IF EXISTS return_origin" not in oauth_return_rollback.read_text():
        raise SystemExit("OAuth return-origin rollback missing return_origin removal")
    xai_subscription = ROOT / "migrations" / "0021_xai_subscription_oauth.sql"
    xai_subscription_rollback = ROOT / "migrations" / "0021_xai_subscription_oauth.rollback.sql"
    if not xai_subscription.is_file() or not xai_subscription_rollback.is_file():
        raise SystemExit("missing SuperGrok OAuth session migration or rollback")
    if "xai.subscription" not in xai_subscription.read_text():
        raise SystemExit("SuperGrok OAuth migration missing xai.subscription")
    if "openai.chatgpt" not in xai_subscription_rollback.read_text():
        raise SystemExit("SuperGrok OAuth rollback must restore ChatGPT-only session check")
    print(
        "canonical, evidence, identity, ontology 0.2/0.3, Habitat, inbound, operator 1.0/1.1, "
        "OT01 acknowledgment, OPEN-025 authorization, OPEN-026 activation, Release Activation "
        "1.1 concurrency, connector credentials, platform OAuth clients, Twilio OAuth, cognitive "
        "credentials, OAuth return origin, SuperGrok OAuth, and control-plane migration integrity verified"
    )


if __name__ == "__main__":
    main()
