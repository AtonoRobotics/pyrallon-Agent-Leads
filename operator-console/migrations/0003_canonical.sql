-- PKT-01 canonical CRM. Payload is the ontology instance. Writes must pass
-- structural + semantic admission in application code before insert/update.

create table if not exists canonical_records (
  tenant_id text not null,
  id text not null,
  record_type text not null,
  schema_version text not null,
  version integer not null,
  status text not null,
  effective_from timestamptz not null,
  effective_to timestamptz,
  payload text not null,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  primary key (tenant_id, id)
);
create index if not exists canonical_records_type_idx
  on canonical_records (tenant_id, record_type, status);

create table if not exists operational_messages (
  tenant_id text not null,
  id text not null,
  journey_id text not null,
  conversation_id text not null,
  direction text not null,
  channel text not null,
  body text not null,
  delivery_state text not null,
  evidence_id text,
  created_at timestamptz not null,
  primary key (tenant_id, id)
);
create index if not exists operational_messages_journey_idx
  on operational_messages (tenant_id, journey_id, created_at);

create table if not exists identity_resolution_cases (
  tenant_id text not null,
  id text not null,
  person_id text not null,
  journey_id text,
  status text not null,
  detail text not null,
  created_at timestamptz not null,
  resolved_at timestamptz,
  resolution_note text,
  primary key (tenant_id, id)
);

create table if not exists tenant_profiles (
  tenant_id text primary key,
  brokerage_name text not null,
  agent_name text not null,
  license_number text not null,
  license_holder_id text not null,
  brokerage_id text not null,
  seeded_at timestamptz
);
