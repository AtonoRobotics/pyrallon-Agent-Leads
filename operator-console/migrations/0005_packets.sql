-- PKT-01 remainder, PKT-02 evidence, PKT-03 Habitat, PKT-04 workflows,
-- PKT-05 ingress idempotency, PKT-06 connectors, observability.

create table if not exists endpoint_index (
  tenant_id text not null,
  endpoint_type text not null,
  normalized_value text not null,
  person_id text not null,
  created_at timestamptz not null,
  primary key (tenant_id, endpoint_type, normalized_value)
);

create table if not exists external_identity_mappings (
  tenant_id text not null,
  mapping_id text not null,
  person_id text not null,
  provider text not null,
  external_id text not null,
  created_at timestamptz not null,
  primary key (tenant_id, mapping_id),
  unique (tenant_id, provider, external_id)
);

create table if not exists evidence_ledger (
  tenant_id text not null,
  seq bigint not null,
  id text not null,
  prev_hash text not null,
  entry_hash text not null,
  artifact_digest text not null,
  payload text not null,
  retention_class text not null,
  record_id text,
  journey_id text,
  created_at timestamptz not null,
  tombstoned_at timestamptz,
  primary key (tenant_id, seq),
  unique (tenant_id, id)
);
create index if not exists evidence_ledger_journey_idx
  on evidence_ledger (tenant_id, journey_id, seq);

create table if not exists evidence_checkpoints (
  tenant_id text not null,
  seq bigint not null,
  chain_hash text not null,
  created_at timestamptz not null,
  primary key (tenant_id, seq)
);

create table if not exists habitat_intents (
  tenant_id text not null,
  intent_id text not null,
  action_class text not null,
  payload text not null,
  payload_digest text not null,
  idempotency_key text not null,
  journey_id text,
  created_at timestamptz not null,
  primary key (tenant_id, intent_id),
  unique (tenant_id, idempotency_key)
);

create table if not exists habitat_locks (
  tenant_id text not null,
  resource_key text not null,
  intent_id text not null,
  acquired_at timestamptz not null,
  expires_at timestamptz not null,
  primary key (tenant_id, resource_key)
);

create table if not exists habitat_permits (
  tenant_id text not null,
  permit_id text not null,
  intent_id text not null,
  action_class text not null,
  payload_digest text not null,
  permit_digest text not null,
  resource_key text not null,
  resource_version text not null,
  status text not null,
  issued_at timestamptz not null,
  expires_at timestamptz not null,
  redeemed_at timestamptz,
  decision text not null,
  reasons text not null,
  primary key (tenant_id, permit_id),
  unique (tenant_id, permit_digest)
);

create table if not exists workflow_instances (
  tenant_id text not null,
  workflow_id text not null,
  workflow_type text not null,
  journey_id text,
  run_id text not null,
  status text not null,
  canonical_version integer not null default 0,
  state text not null,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  primary key (tenant_id, workflow_id)
);

create table if not exists workflow_history (
  tenant_id text not null,
  workflow_id text not null,
  event_seq integer not null,
  event_type text not null,
  event_id text not null,
  payload text not null,
  created_at timestamptz not null,
  primary key (tenant_id, workflow_id, event_seq),
  unique (tenant_id, workflow_id, event_id)
);

create table if not exists workflow_timers (
  tenant_id text not null,
  workflow_id text not null,
  timer_id text not null,
  fire_at timestamptz not null,
  status text not null,
  primary key (tenant_id, workflow_id, timer_id)
);

create table if not exists workflow_leases (
  tenant_id text not null,
  workflow_id text not null,
  owner text not null,
  expires_at timestamptz not null,
  primary key (tenant_id, workflow_id)
);

create table if not exists connector_grants (
  tenant_id text not null,
  connector_id text not null,
  channel text not null,
  status text not null,
  scopes text not null,
  created_at timestamptz not null,
  revoked_at timestamptz,
  primary key (tenant_id, connector_id)
);

create table if not exists inbound_events (
  tenant_id text not null,
  provider_account_ref text not null,
  provider_event_id text not null,
  channel text not null,
  envelope text not null,
  payload_digest text not null,
  journey_id text,
  admitted_at timestamptz not null,
  primary key (tenant_id, provider_account_ref, provider_event_id)
);

create table if not exists operational_events (
  tenant_id text not null,
  id text not null,
  kind text not null,
  journey_id text,
  payload text not null,
  created_at timestamptz not null,
  primary key (tenant_id, id)
);

create table if not exists slot_sets (
  tenant_id text not null,
  slot_set_id text not null,
  journey_id text not null,
  version text not null,
  slots text not null,
  expires_at timestamptz not null,
  created_at timestamptz not null,
  primary key (tenant_id, slot_set_id)
);

create table if not exists gate_evidence (
  gate_id text not null primary key,
  status text not null,
  evidence_refs text not null,
  recorded_at timestamptz not null,
  notes text not null
);
