import { api, ApiError } from "/static/api.js";

const $ = (id) => document.getElementById(id);
const importId = location.pathname.split("/").pop();
let total = 0;

function fail(message) {
  $("heading").textContent = "We couldn't import that conversation";
  $("status").textContent = "";
  $("error").textContent = message;
  $("failed").hidden = false;
}

function excluded() {
  return [...document.querySelectorAll('#messages input[type="checkbox"]')].filter((c) => !c.checked).map((c) => Number(c.value));
}

function updateCount() {
  const left = total - excluded().length;
  $("count").textContent = `${left} of ${total} messages will be added.`;
  for (const id of ["confirm", "confirm-top"]) $(id).disabled = left === 0;
}

function showPreview(p) {
  $("heading").textContent = p.title || "Untitled conversation";
  $("status").textContent = `We found ${p.message_count} message${p.message_count === 1 ? "" : "s"}.`;
  total = p.message_count;
  if (p.warnings.length || !p.complete) {
    $("warning-list").replaceChildren(...p.warnings.map((w) => Object.assign(document.createElement("li"), { textContent: w.message })));
    $("warnings").hidden = false;
  }
  $("messages").replaceChildren(...p.messages.map((m) => {
    const li = Object.assign(document.createElement("li"), { className: `msg ${m.role}` });
    const head = Object.assign(document.createElement("label"), { className: "msg-head" });
    const box = Object.assign(document.createElement("input"), { type: "checkbox", checked: true, value: m.source_index });
    box.addEventListener("change", () => { li.classList.toggle("excluded", !box.checked); updateCount(); });
    head.append(box, ` Include · ${m.author}`);
    const text = Object.assign(document.createElement("div"), { className: "msg-text", textContent: m.text });
    li.append(head, text);
    return li;
  }));
  updateCount();
  $("review").hidden = false;
}

async function poll() {
  for (let i = 0; i < 90; i++) {
    let imp;
    try {
      imp = await api(`/api/imports/${importId}`);
    } catch (e) {
      return fail(e.message);
    }
    if (imp.status === "ready") return showPreview(imp.preview);
    if (imp.status === "failed") return fail(imp.error.message);
    if (imp.status === "imported") { location.href = `/annotate/${imp.conversation_id}`; return; }
    if (imp.status !== "processing") return fail("This import has ended. Please start again.");
    await new Promise((r) => setTimeout(r, 1500));
  }
  fail("This is taking longer than expected. Please try again.");
}

async function confirmImport() {
  for (const id of ["confirm", "confirm-top", "cancel", "cancel-top"]) $(id).disabled = true;
  $("confirm-status").textContent = "Adding your conversation…";
  try {
    const { conversation_id } = await api(`/api/imports/${importId}/confirm`, { method: "POST", body: { exclude: excluded() } });
    location.href = `/annotate/${conversation_id}`;
  } catch (e) {
    if (e instanceof ApiError && e.status === 409 && e.body.conversation_id) {
      location.href = `/annotate/${e.body.conversation_id}`;
      return;
    }
    $("confirm-status").textContent = e.message;
    if (e.status === 502) { setTimeout(() => { location.href = "/conversations"; }, 2500); return; }
    for (const id of ["confirm", "confirm-top", "cancel", "cancel-top"]) $(id).disabled = false;
  }
}

async function cancelImport() {
  await api(`/api/imports/${importId}/cancel`, { method: "POST", body: {} }).catch(() => {});
  location.href = "/conversations";
}

$("confirm").addEventListener("click", confirmImport);
$("confirm-top").addEventListener("click", confirmImport);
$("cancel").addEventListener("click", cancelImport);
$("cancel-top").addEventListener("click", cancelImport);
poll();
