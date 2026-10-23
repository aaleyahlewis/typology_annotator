// CHATGPT_CONVERSATION_IMPORT: find user/assistant messages on a rendered ChatGPT page.
// Shared by tools/share_link_to_labelstudio.js (manual) and app/extract.py (browser fallback).
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

  return dialogue;
})()
