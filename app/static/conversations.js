import { api } from "/static/api.js";
import { loadExport, toConversation } from "/static/chatgpt_export.js";

const $ = (id) => document.getElementById(id);
let exported = [];

async function refresh() {
  const tasks = await api("/api/tasks");
  $("tasks").replaceChildren(...tasks.map((t) => {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = t.title || "Untitled conversation";
    const state = document.createElement("span");
    state.className = "status";
    state.textContent = t.submitted ? " · submitted" : t.updated_at ? " · in progress" : " · not started";
    name.append(state);
    const actions = document.createElement("span");
    actions.className = "row";
    actions.style.marginTop = "0";
    const open = Object.assign(document.createElement("a"), { className: "button", href: `/annotate/${t.id}`, textContent: "Open" });
    const remove = Object.assign(document.createElement("button"), { className: "danger", textContent: "Remove" });
    remove.addEventListener("click", async () => {
      if (!confirm("Remove this conversation and any annotations on it? This can't be undone.")) return;
      await api(`/api/tasks/${t.id}`, { method: "DELETE", body: {} });
      refresh();
    });
    actions.append(open, remove);
    li.append(name, actions);
    return li;
  }));
  $("empty").hidden = tasks.length > 0;
}

function renderChoices(filter = "") {
  const f = filter.toLowerCase();
  const items = exported.filter((c) => c.title.toLowerCase().includes(f)).slice(0, 300);
  const legend = $("choices").querySelector("legend");
  $("choices").replaceChildren(legend, ...items.map((c) => {
    const label = document.createElement("label");
    const input = Object.assign(document.createElement("input"), { type: "radio", name: "conv", value: c.id });
    input.addEventListener("change", () => { $("add").disabled = false; });
    const date = c.created ? new Date(c.created * 1000).toLocaleDateString() : "";
    const text = document.createElement("span");
    text.textContent = `${c.title}`;
    const when = Object.assign(document.createElement("span"), { className: "muted", textContent: date });
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

$("add").addEventListener("click", async () => {
  const id = document.querySelector('input[name="conv"]:checked')?.value;
  const chosen = exported.find((c) => c.id === id);
  if (!chosen) return;
  const conversation = toConversation(chosen.raw);
  try {
    const { id: taskId } = await api("/api/tasks", { method: "POST", body: { conversation, source: "export_file" } });
    exported = [];
    $("file").value = "";
    $("pick").hidden = true;
    location.href = `/annotate/${taskId}`;
  } catch (err) {
    $("file-error").textContent = err.message;
  }
});

$("add-link").addEventListener("click", async () => {
  $("link-status").textContent = "Reading the conversation…";
  try {
    const { id } = await api("/api/share-link", { method: "POST", body: { url: $("url").value } });
    location.href = `/annotate/${id}`;
  } catch (err) {
    $("link-status").textContent = err.message;
  }
});

$("logout").addEventListener("click", async () => {
  await api("/api/logout", { method: "POST", body: {} });
  location.href = "/";
});

api("/api/features").then((f) => { $("link-section").hidden = !f.share_link_import; });
api("/api/me").then((me) => { $("who").textContent = `Signed in as ${me.study_id}`; });
refresh();
