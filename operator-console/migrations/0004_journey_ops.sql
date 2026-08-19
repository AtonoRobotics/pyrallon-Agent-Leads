-- OT-01 workflow view over canonical state. Canonical PostgreSQL records win
-- on replay; this table is a computed/operational projection, not business truth.

create table if not exists journey_ops (
  tenant_id text not null,
  journey_id text not null,
  source_channel text not null,
  source_detail text,
  service_zone text,
  contactability text not null,
  acknowledgment_state text not null,
  consultation_state text not null,
  nurture_state text not null,
  blocker_codes text not null default '[]',
  next_due_at timestamptz,
  primary key (tenant_id, journey_id)
);
