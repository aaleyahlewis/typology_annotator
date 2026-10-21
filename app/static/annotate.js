// Embeds the open-source Label Studio editor with config/labeling_config.xml and
// saves results to this app's own server (no Label Studio server or API involved).
import { api } from "/static/api.js";

const taskId = Number(location.pathname.split("/").pop());
const status = document.getElementById("save-status");
let dirty = false;

async function save(annotation, submitted) {
  const result = annotation.serializeAnnotation();
  status.textContent = "Saving…";
  try {
    await api(`/api/tasks/${taskId}/annotation`, { method: "PUT", body: { result, submitted } });
    dirty = false;
    if (submitted) task.submitted = true;
    status.textContent = submitted ? "Submitted. You can keep editing and press Update to save changes." : `Draft saved ${new Date().toLocaleTimeString()}`;
  } catch (err) {
    status.textContent = `Not saved: ${err.message}`;
  }
}

const [config, task] = await Promise.all([
  fetch("/config/labeling_config.xml", { credentials: "same-origin" }).then((r) => r.text()),
  api(`/api/tasks/${taskId}`),
]);
document.title = task.data.title ? `Annotate: ${task.data.title}` : "Annotate Conversation";

const ls = new window.LabelStudio("label-studio", {
  config,
  interfaces: ["panel", "update", "submit", "controls", "side-column", "edit-history", "topbar"],
  user: { id: 1, pk: 1, firstName: "Participant" },
  task: {
    id: task.id,
    data: task.data,
    annotations: task.result ? [{ id: "saved", result: task.result }] : [],
  },
  onLabelStudioLoad(store) {
    const as = store.annotationStore;
    if (!as.annotations.length) {
      const c = as.addAnnotation({ userGenerate: true });
      as.selectAnnotation(c.id);
    } else {
      as.selectAnnotation(as.annotations[0].id);
    }
  },
  onSubmitAnnotation: (_store, annotation) => save(annotation, true),
  // Reopened work shows "Update" instead of "Submit"; either button counts as submitting.
  onUpdateAnnotation: (_store, annotation) => save(annotation, true),
  onEntityCreate: () => { dirty = true; },
  onEntityDelete: () => { dirty = true; },
});

// Autosave a draft every 30 seconds so work isn't lost if the tab closes.
setInterval(() => {
  const annotation = ls.store?.annotationStore?.selected;
  if (annotation && (dirty || annotation.history?.hasChanges)) save(annotation, task.submitted);
}, 30000);
window.addEventListener("beforeunload", (e) => {
  if (dirty) e.preventDefault();
});
