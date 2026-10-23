// Embeds the open-source Label Studio editor. Results are saved through this app's
// backend, which writes them to Label Studio; the browser never talks to Label Studio.
import { api, ApiError } from "/static/api.js";

const conversationId = location.pathname.split("/").pop();
const status = document.getElementById("save-status");
let version = 0;
let submitted = false;
let saving = null;
let lastSaved = null;   // JSON of the last result the server confirmed

const current = () => ls.store?.annotationStore?.selected;
const unsaved = () => { const a = current(); return a && JSON.stringify(a.serializeAnnotation()) !== lastSaved; };

function say(text) { status.textContent = text; }

async function save(annotation, submit) {
  if (saving) await saving;
  const result = annotation.serializeAnnotation();
  say("Saving…");
  saving = (async () => {
    try {
      const r = await api(`/api/conversations/${conversationId}/annotation`, {
        method: "PUT", body: { result, version, submitted: submit },
      });
      version = r.version;
      lastSaved = JSON.stringify(result);
      if (submit) submitted = true;
      say(submit ? "Saved. You can keep editing; press Update to save changes." : `Draft saved at ${new Date().toLocaleTimeString()}`);
    } catch (err) {
      // Never report success unless the server confirmed it.
      say(err instanceof ApiError && err.status === 409 ? err.message : `Not saved. ${err.message}`);
    } finally {
      saving = null;
    }
  })();
  return saving;
}

let conv;
try {
  conv = await api(`/api/conversations/${conversationId}`);
} catch (err) {
  document.getElementById("label-studio").textContent = err.message;
  throw err;
}
const config = await fetch("/config/labeling_config.xml", { credentials: "same-origin" }).then((r) => r.text());
version = conv.version;
submitted = conv.submitted;
document.title = conv.title ? `Annotate: ${conv.title}` : "Annotate Conversation";
const dialogue = conv.data.dialogue;

const ls = new window.LabelStudio("label-studio", {
  config,
  interfaces: ["panel", "update", "submit", "controls", "side-column", "edit-history", "topbar"],
  user: { id: 1, pk: 1, firstName: "Participant" },
  task: { id: 1, data: conv.data, annotations: conv.result ? [{ id: "saved", result: conv.result }] : [] },
  onLabelStudioLoad(store) {
    const as = store.annotationStore;
    if (!as.annotations.length) {
      const c = as.addAnnotation({ userGenerate: true });
      as.selectAnnotation(c.id);
    } else {
      as.selectAnnotation(as.annotations[0].id);
    }
    setTimeout(() => { lastSaved = JSON.stringify(current()?.serializeAnnotation() ?? []); });
  },
  onSubmitAnnotation: (_s, annotation) => save(annotation, true),
  // Reopened work shows "Update" instead of "Submit"; both save and count as submitted.
  onUpdateAnnotation: (_s, annotation) => save(annotation, true),
  onEntityCreate: (region) => {
    // A highlight must stay inside one message. When a drag crosses into another message the
    // editor keeps only the part in the second message, under the first message's label, so
    // drop any highlight whose label doesn't match its message. The editor fills in the
    // position just after this callback, so check on the next tick.
    setTimeout(() => {
      if (!region || region.start === undefined) return;
      const msg = dialogue[Number(region.start)];
      const label = region.labeling?.selectedLabels?.[0]?.value;
      const expected = msg && (msg.role === "user" ? conv.labels.user : conv.labels.assistant);
      if (String(region.start) !== String(region.end) || (label && expected && label !== expected)) {
        if (typeof region.deleteRegion === "function") region.deleteRegion();
        else region.annotation?.deleteRegion?.(region);
        say("Please highlight within a single message.");
      }
    }, 0);
  },
});

// Choose "my text" / "AI's text" automatically from the message the participant starts
// highlighting in, so the label always matches who wrote the message.
function phraseIndex(target) {
  const phrase = target.closest && target.closest('[class*="phrase--"]');
  if (!phrase) return -1;
  return [...document.querySelectorAll('[class*="phrase--"]')].indexOf(phrase);
}

function selectLabelForRole(role) {
  const ann = ls.store?.annotationStore?.selected;
  const control = ann?.names?.get(conv.labels.control);
  if (!control?.children) return;
  const want = role === "user" ? conv.labels.user : conv.labels.assistant;
  for (const label of control.children) {
    const on = label.value === want;
    if (Boolean(label.selected) !== on && typeof label.setSelected === "function") label.setSelected(on);
  }
}

document.getElementById("label-studio").addEventListener("mousedown", (e) => {
  const idx = phraseIndex(e.target);
  if (idx >= 0 && dialogue[idx]) selectLabelForRole(dialogue[idx].role);
}, true);

// Autosave a draft every 20 seconds while there are unsaved changes.
setInterval(() => {
  if (!saving && lastSaved !== null && unsaved()) save(current(), submitted);
}, 20000);
window.addEventListener("beforeunload", (e) => { if (lastSaved !== null && unsaved()) e.preventDefault(); });
window.typologyEditor = ls;  // used by the browser tests
