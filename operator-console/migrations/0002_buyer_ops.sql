-- Canonical buyer-ops records for OT-01 operator surface.
-- All identifiers are text (Better Auth / preview-safe). Every row is tenant-scoped via user_id.

create table if not exists ops_tenants (
  user_id text primary key,
  brokerage_name text not null,
  agent_name text not null,
  license_number text,
  service_zones text not null default '["San Antonio","Austin","Fredericksburg"]',
  seeded_at timestamptz
);

create table if not exists ops_people (
  id text primary key,
  user_id text not null,
  full_name text not null,
  email text,
  phone text,
  identity_state text not null default 'resolved',
  created_at timestamptz not null default now()
);
create index if not exists ops_people_user_idx on ops_people (user_id);

create table if not exists ops_journeys (
  id text primary key,
  user_id text not null,
  person_id text not null,
  source text not null,
  source_detail text,
  journey_state text not null,
  contactability text not null,
  acknowledgment text not null,
  qualification_state text not null,
  consultation_state text not null,
  nurture_state text not null,
  representation_state text not null,
  service_zone text,
  next_due_at timestamptz,
  blocker_codes text not null default '[]',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists ops_journeys_user_idx on ops_journeys (user_id);
create index if not exists ops_journeys_person_idx on ops_journeys (person_id);

create table if not exists ops_messages (
  id text primary key,
  user_id text not null,
  journey_id text not null,
  direction text not null,
  channel text not null,
  body text not null,
  delivery_state text,
  created_at timestamptz not null default now()
);
create index if not exists ops_messages_journey_idx on ops_messages (journey_id);

create table if not exists ops_observations (
  id text primary key,
  user_id text not null,
  journey_id text not null,
  criterion text not null,
  epistemic_type text not null,
  value text not null,
  source_label text,
  created_at timestamptz not null default now()
);
create index if not exists ops_observations_journey_idx on ops_observations (journey_id);

create table if not exists ops_appointments (
  id text primary key,
  user_id text not null,
  journey_id text not null,
  appointment_type text not null,
  starts_at timestamptz not null,
  location_or_mode text not null,
  state text not null,
  created_at timestamptz not null default now()
);
create index if not exists ops_appointments_user_idx on ops_appointments (user_id);

create table if not exists ops_exceptions (
  id text primary key,
  user_id text not null,
  journey_id text,
  kind text not null,
  title text not null,
  detail text not null,
  status text not null default 'open',
  created_at timestamptz not null default now()
);
create index if not exists ops_exceptions_user_idx on ops_exceptions (user_id, status);

create table if not exists ops_evidence (
  id text primary key,
  user_id text not null,
  journey_id text,
  record_type text not null,
  summary text not null,
  digest text not null,
  created_at timestamptz not null default now()
);
create index if not exists ops_evidence_journey_idx on ops_evidence (journey_id);

create table if not exists ops_consent (
  id text primary key,
  user_id text not null,
  person_id text not null,
  channel text not null,
  purpose text not null,
  status text not null,
  basis text not null,
  created_at timestamptz not null default now()
);

create table if not exists ops_iabs (
  id text primary key,
  user_id text not null,
  journey_id text not null,
  form_version text not null,
  channel text not null,
  validity_state text not null,
  delivered_at timestamptz
);

create table if not exists ops_actions (
  id text primary key,
  user_id text not null,
  journey_id text,
  exception_id text,
  action text not null,
  note text,
  created_at timestamptz not null default now()
);
