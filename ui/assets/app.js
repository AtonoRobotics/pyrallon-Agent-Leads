const state = { token: "", tenant: "", actor: "" };
const $ = (id) => document.getElementById(id);
const escapeHtml = (value) => String(value ?? "").replace(/[&<>\"']/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;",
}[character]));

function setStatus(text, kind = "neutral") {
  const node = $("connection-status");
  node.textContent = text;
  node.className = `status status-${kind}`;
}

function message(value) {
  $("system-message").textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function persistConnection() {
  sessionStorage.setItem("buyer-ops-connection", JSON.stringify({
    token: state.token,
    tenant: state.tenant,
    actor: state.actor,
  }));
}

function restoreConnection() {
  try {
    const saved = JSON.parse(sessionStorage.getItem("buyer-ops-connection") || "null");
    if (!saved || typeof saved !== "object") return false;
    if (!["token", "tenant", "actor"].every((key) => typeof saved[key] === "string" && saved[key])) return false;
    state.token = saved.token;
    state.tenant = saved.tenant;
    state.actor = saved.actor;
    $("control-token").value = state.token;
    $("tenant-id").value = state.tenant;
    $("actor-id").value = state.actor;
    return true;
  } catch (_error) {
    sessionStorage.removeItem("buyer-ops-connection");
    return false;
  }
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    method: options.method || "GET",
    body: options.body ? JSON.stringify(options.body) : undefined,
    headers: {
      "x-buyer-ops-token": state.token,
      "x-buyer-ops-tenant": state.tenant,
      "x-buyer-ops-actor": state.actor,
      ...(options.body ? { "content-type": "application/json" } : {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.detail || payload.safe_detail || "request failed");
    error.payload = payload;
    error.status = response.status;
    throw error;
  }
  return payload;
}

async function submit(path, body) {
  const payload = await request(path, { method: "POST", body });
  message({ status: "ok", path, result: payload });
  return payload;
}

function renderSummary(journeys) {
  const items = journeys?.journeys || [];
  const states = items.map((item) => item.state || {});
  const blocked = states.filter((item) => (item.blocker_codes || []).length > 0).length;
  $("summary-cards").innerHTML = [
    [items.length, "Journeys"],
    [blocked, "With blockers"],
    [states.filter((item) => item.contactability_state === "contactable").length, "Contactable"],
    [states.filter((item) => item.qualification_state === "ready").length, "Qualification ready"],
  ].map(([value, label]) => `<div class="card"><strong>${value}</strong><span>${label}</span></div>`).join("");
}

function renderJourneys(payload) {
  const items = payload?.journeys || [];
  $("journeys").innerHTML = items.length ? items.map((item) => {
    const state = item.state || {};
    return `<article class="journey"><h3>${escapeHtml(item.journey_id || "Unnamed journey")}</h3><dl>
      <div><dt>Ingress</dt><dd>${escapeHtml(state.ingress_state || "unknown")}</dd></div>
      <div><dt>Contactability</dt><dd>${escapeHtml(state.contactability_state || "unknown")}</dd></div>
      <div><dt>Qualification</dt><dd>${escapeHtml(state.qualification_state || "unknown")}</dd></div>
      <div><dt>Consultation</dt><dd>${escapeHtml(state.consultation_state || "unknown")}</dd></div>
      <div><dt>Canonical version</dt><dd>${escapeHtml(state.canonical_version ?? "unknown")}</dd></div>
      <div><dt>Blockers</dt><dd>${escapeHtml((state.blocker_codes || []).join(", ") || "none")}</dd></div>
    </dl></article>`;
  }).join("") : '<p class="empty">No current journeys.</p>';
}

function renderRows(target, rows, empty, fields) {
  const node = $(target);
  if (!rows || !rows.length) {
    node.innerHTML = `<p class="empty">${escapeHtml(empty)}</p>`;
    return;
  }
  node.innerHTML = rows.map((row) => `<article class="data-card"><dl>${fields.map(([label, key]) =>
    `<div><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(row?.[key] ?? "unknown")}</dd></div>`).join("")}</dl></article>`).join("");
}

function renderConnectors(payload) {
  renderRows("connectors", payload?.connectors || [], "No governed provider connections.", [
    ["Connector", "connector_id"], ["Provider", "provider_kind"], ["Authorization", "authorization"],
    ["Grant", "grant_state"], ["Capabilities", "capabilities"],
  ]);
}

function renderPlatformOAuth(payload) {
  const clients = payload?.clients || [];
  const configured = clients.map((client) => client.issuer).join(", ") || "none";
  const redirectUri = payload?.redirectUri || "";
  $("platform-oauth-client-result").textContent = `Registered provider applications: ${configured}.`;
  if (redirectUri && !$("oauth-redirect-uri").value) $("oauth-redirect-uri").value = redirectUri;
  if (payload?.publicOrigin && !$("oauth-return-origin").value) $("oauth-return-origin").value = payload.publicOrigin;
}

function renderCognition(payload) {
  renderRows("cognition-identities", payload?.identities || [], "No cognitive identities configured.", [
    ["Identity", "identity_ref"], ["Runtime", "runtime_class"], ["Provider", "provider_id"],
    ["State", "state"], ["Policy", "data_policy_version"],
  ]);
}

function renderActivation(payload) {
  renderRows("activation", payload?.decisions || [], "No activation decisions recorded.", [
    ["Gate", "gate_id"], ["State", "decision"], ["Version", "decision_version"],
    ["Effective", "effective_at"], ["Evidence", "evidence_digest"],
  ]);
}

async function loadWorkspace() {
  setStatus("Loading", "neutral");
  message("Loading current canonical workspace...");
  try {
    const results = await Promise.allSettled([
      request("/v1/workspace"),
      request("/v1/connectors"),
      request("/v1/platform/oauth-clients"),
      request("/v1/cognition/identities"),
      request("/v1/activation"),
    ]);
    const payload = results[0].status === "fulfilled" ? results[0].value : null;
    if (!payload) throw results[0].reason;
    renderSummary(payload);
    renderJourneys(payload);
    renderConnectors(results[1].status === "fulfilled" ? results[1].value : null);
    renderPlatformOAuth(results[2].status === "fulfilled" ? results[2].value : null);
    renderCognition(results[3].status === "fulfilled" ? results[3].value : null);
    renderActivation(results[4].status === "fulfilled" ? results[4].value : null);
    const failures = results.slice(1).filter((result) => result.status === "rejected").map((result) => result.reason?.payload || result.reason?.message);
    $("summary-detail").textContent = "Loaded from the authenticated control plane.";
    setStatus("Connected", "good");
    $("refresh-button").disabled = false;
    message({ status: failures.length ? "partial" : "ok", loaded: new Date().toISOString(), failures });
  } catch (error) {
    setStatus(`Unavailable (${error.status || "error"})`, "bad");
    $("summary-detail").textContent = "The control plane returned a typed failure.";
    message(error.payload || error.message);
    $("refresh-button").disabled = false;
  }
}

$("connection-form").addEventListener("submit", (event) => {
  event.preventDefault();
  state.token = $("control-token").value;
  state.tenant = $("tenant-id").value;
  state.actor = $("actor-id").value;
  persistConnection();
  loadWorkspace();
});
$("refresh-button").addEventListener("click", loadWorkspace);

$("platform-oauth-client-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const secret = $("platform-oauth-client-secret");
  try {
    const payload = await submit("/v1/platform/oauth-clients", {
      issuer: $("platform-oauth-issuer").value,
      clientId: $("platform-oauth-client-id").value.trim(),
      clientSecret: secret.value,
      directoryId: $("platform-oauth-directory-id").value.trim() || undefined,
    });
    secret.value = "";
    $("platform-oauth-client-result").textContent = `Provider application registered for ${payload.issuer}. The secret is not displayed or retained in this page.`;
    await loadWorkspace();
  } catch (error) {
    secret.value = "";
    $("platform-oauth-client-result").textContent = JSON.stringify(error.payload || error.message, null, 2);
    message(error.payload || error.message);
  }
});

$("provider-oauth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const payload = await submit("/v1/connectors/oauth/start", {
      connectorId: $("oauth-connector-id").value.trim(),
      redirectUri: $("oauth-redirect-uri").value.trim(),
      returnOrigin: $("oauth-return-origin").value.trim(),
    });
    $("oauth-result").textContent = "Authorization session created. Continue at the provider authorization URL.";
    if (payload.authorizationUrl) window.open(payload.authorizationUrl, "buyer-ops-provider-auth", "noopener");
  } catch (error) {
    $("oauth-result").textContent = JSON.stringify(error.payload || error.message, null, 2);
    message(error.payload || error.message);
  }
});

async function completeOAuthCallback() {
  const query = new URLSearchParams(window.location.search);
  const code = query.get("code");
  const oauthState = query.get("state");
  const providerError = query.get("error");
  if (!code && !oauthState && !providerError) return;
  window.history.replaceState({}, document.title, window.location.pathname + window.location.hash);
  if (providerError) {
    $("oauth-result").textContent = `Provider authorization failed: ${providerError}`;
    return;
  }
  if (!code || !oauthState) {
    $("oauth-result").textContent = "Provider callback is missing code or state.";
    return;
  }
  if (!state.token || !state.tenant || !state.actor) {
    $("oauth-result").textContent = "Reconnect the authenticated workspace before completing provider authorization.";
    return;
  }
  try {
    const payload = await submit("/v1/connectors/oauth/complete", { code, state: oauthState });
    $("oauth-result").textContent = "Provider authorization completed and encrypted binding saved.";
    message({ status: "ok", provider: payload.provider, connectorId: payload.connectorId });
    await loadWorkspace();
  } catch (error) {
    $("oauth-result").textContent = JSON.stringify(error.payload || error.message, null, 2);
    message(error.payload || error.message);
  }
}

if (restoreConnection()) {
  loadWorkspace();
}
completeOAuthCallback();

$("cognition-metered-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await submit("/v1/cognition/metered", {
      connectorId: $("metered-connector-id").value.trim(),
      apiKey: $("metered-api-key").value,
    });
    $("cognition-setup-result").textContent = "Metered provider binding submitted; secret is not rendered.";
    event.target.reset();
    await loadWorkspace();
  } catch (error) {
    $("cognition-setup-result").textContent = JSON.stringify(error.payload || error.message, null, 2);
    message(error.payload || error.message);
  }
});

$("cognition-local-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await submit("/v1/cognition/local", {
      baseUrl: $("local-base-url").value.trim(),
      modelId: $("local-model-id").value.trim(),
      token: $("local-token").value,
    });
    $("cognition-setup-result").textContent = "Local runtime binding submitted; token is not rendered.";
    event.target.reset();
    await loadWorkspace();
  } catch (error) {
    $("cognition-setup-result").textContent = JSON.stringify(error.payload || error.message, null, 2);
    message(error.payload || error.message);
  }
});
