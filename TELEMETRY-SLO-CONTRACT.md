# Telemetry and SLO Contract 1.0

`TELEMETRY-SLO-CATALOG.json` is the sole identity and calculation catalog;
`TELEMETRY-SLO.schema.json` governs observations, evaluations, dashboards, and alerts. Producers may
emit only catalog metric identifiers with their declared unit and exact start/end events. Ratios use
the declared numerator and denominator; latency is non-negative elapsed event time. Late events are
recomputed into their original window and produce a new evaluation linked by source digest rather
than mutating prior evidence.

Only `environment`, `region`, `channel`, `provider`, and `result` are dimensions. Tenant, person,
journey, message, and free-text labels are prohibited, and a metric is blocked at 5,000 series.
Rolling windows are half-open `[start,end)` in UTC. Percentiles use nearest-rank over admitted
observations. A ratio with a zero denominator or any SLO below `minimumSamples` is
`insufficient_data`, never pass. Evaluations bind their catalog version, source digest, sample count,
window, statistic, comparator, objective, and calculated error-budget consumption.

The catalog’s thresholds are product operational objectives, not legal conclusions. In particular,
counterfactual equality is a release invariant: any non-zero measured decision difference blocks
promotion and routes to broker operations; it is not represented as a statutory disparate-impact
threshold. Warning and exhausted-budget alerts route as cataloged, remain owned until resolved, and
are retained for seven years. Raw operational observations use the catalog retention class; audit,
SLO, activation, complaint, opt-out, and safety evidence is retained for seven years subject to the
governing legal-hold and deletion contract.

