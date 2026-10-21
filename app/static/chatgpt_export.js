// CHATGPT_CONVERSATION_IMPORT: read a ChatGPT data export in the browser.
// Accepts the export .zip or the conversations.json inside it. Nothing is uploaded
// here: the page only sends the one conversation the participant picks.
// Same logic as tools/chatgpt_to_labelstudio.py.

const KEEP_ROLES = new Set(["user", "assistant"]);

async function readZipEntry(file, wanted) {
  // Minimal zip reader: find `wanted` via the central directory and inflate it.
  const buf = new Uint8Array(await file.arrayBuffer());
  const view = new DataView(buf.buffer);
  let eocd = -1;
  for (let i = buf.length - 22; i >= Math.max(0, buf.length - 65557); i--) {
    if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error("This file is not a valid zip.");
  let count = view.getUint16(eocd + 10, true);
  let ptr = view.getUint32(eocd + 16, true);
  const dec = new TextDecoder();
  while (count--) {
    if (view.getUint32(ptr, true) !== 0x02014b50) break;
    const method = view.getUint16(ptr + 10, true);
    const csize = view.getUint32(ptr + 20, true);
    const nameLen = view.getUint16(ptr + 28, true), extraLen = view.getUint16(ptr + 30, true), commentLen = view.getUint16(ptr + 32, true);
    const local = view.getUint32(ptr + 42, true);
    const name = dec.decode(buf.subarray(ptr + 46, ptr + 46 + nameLen));
    ptr += 46 + nameLen + extraLen + commentLen;
    if (name.split("/").pop() !== wanted) continue;
    const dataStart = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    const raw = buf.subarray(dataStart, dataStart + csize);
    if (method === 0) return dec.decode(raw);
    if (method !== 8) throw new Error("Unsupported zip compression.");
    const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate-raw"));
    return await new Response(stream).text();
  }
  throw new Error(`Couldn't find ${wanted} in the zip. Is this a ChatGPT data export?`);
}

export async function loadExport(file) {
  const text = file.name.toLowerCase().endsWith(".zip") ? await readZipEntry(file, "conversations.json") : await file.text();
  let convs;
  try { convs = JSON.parse(text); } catch { throw new Error("That file isn't valid JSON."); }
  if (!Array.isArray(convs)) convs = [convs];
  if (!convs.every((c) => c && typeof c.mapping === "object")) throw new Error("That doesn't look like a ChatGPT conversations.json file.");
  return convs
    .map((c) => ({ id: c.conversation_id || c.id, title: c.title || "(untitled)", created: c.create_time || 0, raw: c }))
    .sort((a, b) => b.created - a.created);
}

function messageText(msg) {
  const content = msg.content || {};
  if (content.content_type && !["text", "multimodal_text"].includes(content.content_type)) return "";
  return (content.parts || [])
    .map((p) => (typeof p === "string" ? p : p && p.content_type === "image_asset_pointer" ? "[image]" : ""))
    .filter(Boolean).join("\n").trim();
}

export function toConversation(conv) {
  // Walk from current_node to the root so edited/regenerated branches are dropped.
  const chain = [];
  for (let id = conv.current_node; id; id = conv.mapping[id]?.parent) chain.unshift(conv.mapping[id]);
  const dialogue = [];
  let last = 0;
  for (const node of chain) {
    const msg = node && node.message;
    if (!msg) continue;
    const role = msg.author && msg.author.role;
    if (!KEEP_ROLES.has(role) || (msg.metadata && msg.metadata.is_visually_hidden_from_conversation)) continue;
    const text = messageText(msg);
    if (!text) continue;
    const ms = Math.max(Math.round((msg.create_time || conv.create_time || 0) * 1000), last + 1);
    last = ms;
    dialogue.push({ message_id: msg.id, index: dialogue.length, role, text, createdAt: ms });
  }
  return { conversation_id: conv.conversation_id || conv.id, title: conv.title, dialogue };
}
