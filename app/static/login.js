import { api } from "/static/api.js";

const form = document.getElementById("login");
const error = document.getElementById("error");

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  error.textContent = "";
  try {
    await api("/api/login", { method: "POST", body: { study_id: form.study_id.value, code: form.code.value } });
    location.href = "/conversations";
  } catch (err) {
    error.textContent = err.message;
  }
});
