import { api, ApiError } from "/static/api.js";
import { loadExport, toConversation } from "/static/chatgpt_export.js";

const $ = (id) => document.getElementById(id);
let exported = [];

const STATUS_TEXT = { creating: "setting up…", task_failed: "setup didn't finish", ready: "" };

async function refresh() {
  const items = await api("/api/conversations");
  $("list").replaceChildren(...items.map((c) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = c.title || "Untitled conversation";
    const state = Object.assign(document.createElement("span"), { className: "status" });
    state.textContent = c.status !== "ready" ? ` · ${STATUS_TEXT[c.status] || c.status}`
      : c.submitted_at ? " · submitted" : c.annotated_at ? " · in progress" : " · not started";
    name.append(state);
    const actions = Object.assign(document.createElement("span"), { className: "row" });
    actions.style.marginTop = "0";
    if (c.status === "ready") {
      actions.append(Object.assign(document.createElement("a"), { className: "button", href: `/annotate/${c.id}`, textContent: "Open" }));
    } else if (c.status === "task_failed") {
      const retry = Object.assign(document.createElement("button"), { textContent: "Try again" });
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        try { await api(`/api/conversations/${c.id}/retry`, { method: "POST", body: {} }); } catch (e) { alert(e.message); }
        refresh();
      });
      actions.append(retry);
    }
    const remove = Object.assign(document.createElement("button"), { className: "danger", textContent: "Remove" });
    remove.setAttribute("aria-label", `Remove ${c.title || "conversation"}`);
    remove.addEventListener("click", async () => {
      if (!confirm("Remove this conversation and all your annotations on it? This can't be undone.")) return;
      try { await api(`/api/conversations/${c.id}`, { method: "DELETE", body: {} }); } catch (e) { alert(e.message); }
      refresh();
    });
    actions.append(remove);
    li.append(name, actions);
    return li;
  }));
  $("empty").hidden = items.length > 0;
}

function goToExisting(err) {
  if (err instanceof ApiError && err.status === 409 && err.body.conversation_id) {
    if (confirm(`${err.body.detail} Open it now?`)) location.href = `/annotate/${err.body.conversation_id}`;
    return true;
  }
  return false;
}

$("link-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const status = $("link-status");
  $("add-link").disabled = true;
  status.textContent = "Checking the link…";
  try {
    const { import_id } = await api("/api/conversations/import", { method: "POST", body: { shared_url: $("url").value.trim() } });
    location.href = `/import/${import_id}`;
  } catch (err) {
    if (!goToExisting(err)) status.textContent = err.message;
    $("add-link").disabled = false;
  }
});

function renderChoices(filter = "") {
  const f = filter.toLowerCase();
  const items = exported.filter((c) => c.title.toLowerCase().includes(f)).slice(0, 300);
  const legend = $("choices").querySelector("legend");
  $("choices").replaceChildren(legend, ...items.map((c) => {
    const label = document.createElement("label");
    const input = Object.assign(document.createElement("input"), { type: "radio", name: "conv", value: c.id });
    input.addEventListener("change", () => { $("add-file").disabled = false; });
    const text = document.createElement("span");
    text.textContent = c.title;
    const when = Object.assign(document.createElement("span"), { className: "muted",
      textContent: c.created ? new Date(c.created * 1000).toLocaleDateString() : "" });
    label.append(input, text, when);
    return label;
  }));
}

$("file").addEventListener("change", async (e) => {
  $("file-error").textContent = "";
  $("pick").hidden = true;
  const file = e.target.files[0];
  if (!file) return;
  try {
    exported = await loadExport(file);
    renderChoices();
    $("pick").hidden = false;
  } catch (err) {
    $("file-error").textContent = err.message;
  }
});

$("search").addEventListener("input", (e) => renderChoices(e.target.value));

$("add-file").addEventListener("click", async () => {
  const id = document.querySelector('input[name="conv"]:checked')?.value;
  const chosen = exported.find((c) => c.id === id);
  if (!chosen) return;
  try {
    const { import_id } = await api("/api/conversations/import-file", { method: "POST", body: { conversation: toConversation(chosen.raw) } });
    exported = [];
    location.href = `/import/${import_id}`;
  } catch (err) {
    if (!goToExisting(err)) $("file-error").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: {} });
  location.href = "/";
});

api("/api/me").then((me) => { $("who").textContent = `Signed in as ${me.study_id}`; });
refresh();
