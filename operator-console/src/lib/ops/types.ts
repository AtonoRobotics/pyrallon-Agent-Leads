export type Tenant = {
  tenantId: string;
  brokerageName: string;
  agentName: string;
  licenseNumber: string;
  licenseHolderId: string;
  brokerageId: string;
};

export type PersonView = {
  id: string;
  displayName: string;
  identityState: string;
  email: string | null;
  phone: string | null;
};

export type JourneyView = {
  id: string;
  personId: string;
  buyingPartyId: string;
  journeyState: string;
  qualificationState: string;
  representationState: string;
  source: string;
  sourceDetail: string | null;
  serviceZone: string | null;
  contactability: string;
  acknowledgment: string;
  consultationState: string;
  nurtureState: string;
  blockerCodes: string[];
  createdAt: string;
  updatedAt: string;
};

export type JourneyCard = JourneyView & {
  person: PersonView;
  openCases: number;
  nextAppointment: {
    id: string;
    startsAt: string;
    state: string;
    locationOrMode: string | null;
  } | null;
};

export type MessageView = {
  id: string;
  direction: string;
  channel: string;
  body: string;
  deliveryState: string;
  createdAt: string;
};

export type ObservationView = {
  id: string;
  criterion: string;
  epistemicType: string;
  value: string;
  observationState: string;
  sourceLabel: string | null;
};

export type AppointmentView = {
  id: string;
  journeyId: string;
  startsAt: string;
  endsAt: string;
  state: string;
  locationOrMode: string | null;
};

export type CaseView = {
  id: string;
  journeyId: string | null;
  kind: string;
  title: string;
  detail: string;
  status: string;
  createdAt: string;
};

export type EvidenceView = {
  id: string;
  summary: string;
  epistemicType: string;
};

export type ConsentView = {
  id: string;
  channel: string;
  purpose: string;
  status: string;
  basis: string;
};

export type Activation = {
  habitat: "in_process";
  temporal: "not_temporal_cloud";
  cognition: "deterministic_local";
  connectors: {
    form: "active";
    email: "inactive";
    sms: "inactive";
    calendar: "inactive";
    voice: "prohibited";
  };
};

export type JourneyDetail = {
  tenant: Tenant;
  journey: JourneyView;
  person: PersonView;
  messages: MessageView[];
  observations: ObservationView[];
  appointments: AppointmentView[];
  cases: CaseView[];
  evidence: EvidenceView[];
  consent: ConsentView[];
  commitments: Array<{ id: string; description: string; state: string; dueAt: string }>;
  activation: Activation;
  workflow?: {
    workflowId: string;
    state: {
      ingressState: string;
      acknowledgmentState: string;
      qualificationState: string;
      consultationState: string;
      nurtureState: string;
      blockerCodes: string[];
    };
  };
  ledger?: Array<{ id: string; seq: number; kind: string }>;
};

export type DashboardPayload = {
  tenant: Tenant;
  journeys: JourneyCard[];
  cases: CaseView[];
  appointments: AppointmentView[];
  stats: {
    active: number;
    ready: number;
    proposed: number;
    openCases: number;
    suppressed: number;
  };
  activation: Activation;
};

export const CRITERION_LABELS: Record<string, string> = {
  identity: "Identity",
  representation: "Existing representation",
  purchase_intent: "Purchase intent",
  geography: "Target geography",
  property: "Property needs",
  timing: "Timing",
  budget_financing: "Budget and financing",
  contingency: "Sale contingency",
  decision_participants: "Decision participants",
  scheduling: "Scheduling",
  channel: "Preferred channel",
};

export const SOURCE_LABELS: Record<string, string> = {
  form: "Branded form",
  email: "Inbound email",
  sms: "Inbound SMS",
  referral: "Referral",
};
