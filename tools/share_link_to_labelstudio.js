// CHATGPT_CONVERSATION_IMPORT (share-link route)
// Run in the browser console on an open ChatGPT conversation or share page.
// Downloads a Label Studio task file for labeling_config.xml. Nothing is sent anywhere.
(() => {
  const ROLE_AUTHOR = { user: "You", assistant: "ChatGPT" };
  const roleFromLabel = (t) => /^\s*(you said|you)\b/i.test(t) ? "user" : /^\s*chatgpt( said)?\b/i.test(t) ? "assistant" : null;

  // ChatGPT has marked messages up in different ways over time; try each, newest first.
  const strategies = [
    () => [...document.querySelectorAll("[data-message-author-role]")]
      .map((el) => ({ el, role: el.getAttribute("data-message-author-role"), id: el.getAttribute("data-message-id") })),
    () => [...document.querySelectorAll("[data-turn]")]
      .map((el) => ({ el, role: el.getAttribute("data-turn"), id: el.querySelector("[data-message-id]")?.getAttribute("data-message-id") || el.getAttribute("data-turn-id") })),
    () => [...document.querySelectorAll('[data-testid^="conversation-turn"]')].map((el) => {
      const label = el.querySelector("h5, h6, .sr-only")?.textContent || "";
      return { el, role: roleFromLabel(label), id: el.querySelector("[data-message-id]")?.getAttribute("data-message-id") };
    }),
  ];

  const readable = (el) => {
    const copy = el.cloneNode(true);
    copy.querySelectorAll("ol").forEach((ol) =>
      [...ol.children].forEach((li, i) => li.prepend(`${(+ol.getAttribute("start") || 1) + i}. `)));
    copy.querySelectorAll("ul > li").forEach((li) => li.prepend("• "));
    copy.querySelectorAll("button, svg, [role=button]").forEach((b) => b.remove());
    // Drop screen-reader labels such as "You said:" / "ChatGPT said:".
    copy.querySelectorAll(".sr-only, h5, h6").forEach((h) => { if (h.matches(".sr-only") || roleFromLabel(h.textContent)) h.remove(); });
    copy.style.cssText = "position:fixed;left:-99999px;top:0;white-space:normal";
    document.body.appendChild(copy);
    const text = copy.innerText;
    copy.remove();
    return (text || "").replace(/\n{3,}/g, "\n\n").trim();
  };

  let dialogue = [];
  for (const find of strategies) {
    dialogue = [];
    for (const { el, role, id } of find()) {
      if (!(role in ROLE_AUTHOR)) continue;
      const text = readable(el);
      if (!text) continue;
      dialogue.push({ message_id: id || `msg-${dialogue.length + 1}`, index: dialogue.length, role, author: ROLE_AUTHOR[role], text });
    }
    if (dialogue.length >= 2) break;
  }

  if (dialogue.length < 2) {
    // Diagnostic only: element counts and attribute names, never message text.
    const attrs = {};
    document.querySelectorAll("main *").forEach((el) => [...el.attributes].forEach((a) => {
      if (/^data-|^role$/.test(a.name)) attrs[a.name] = (attrs[a.name] || 0) + 1;
    }));
    console.log("TYPOLOGY_DIAGNOSTIC " + JSON.stringify({
      url_type: location.pathname.split("/")[1],
      main: !!document.querySelector("main"), articles: document.querySelectorAll("article").length,
      testids: [...new Set([...document.querySelectorAll("[data-testid]")].map((e) => e.getAttribute("data-testid").replace(/\d+/g, "N")))].slice(0, 40),
      attrs,
    }));
    alert(`Found ${dialogue.length} message(s). Copy the line starting with TYPOLOGY_DIAGNOSTIC from the console and send it to the research team. It contains no conversation text.`);
    return;
  }

  const id = location.pathname.split("/").filter(Boolean).pop();
  const participant = prompt("Participant study ID (e.g. P01):", "") || null;
  const tasks = [{ data: {
    max: dialogue.length, conversation_id: id,
    title: document.title.replace(/\s*[-|]\s*ChatGPT\s*$/i, "") || null,
    participant_id: participant, codebook_version: "typology-v1",
    source: location.pathname.startsWith("/share/") ? "share_link" : "conversation_page", dialogue,
  } }];
  const blob = new Blob([JSON.stringify(tasks, null, 2)], { type: "application/json" });
  const a = Object.assign(document.createElement("a"), {
    href: URL.createObjectURL(blob), download: `${participant || "participant"}_${id}_tasks.json`,
  });
  document.body.appendChild(a); a.click(); a.remove();
  console.log(`Saved ${dialogue.length} messages (${dialogue.filter((m) => m.role === "user").length} from the participant).`);
})();
