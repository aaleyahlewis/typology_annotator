// End-to-end participant flow in a real browser against the app, a real Label Studio
// server, and the local share-page stand-in. Run via scripts/run_e2e.sh.
const { chromium } = require(process.env.PLAYWRIGHT_PATH || "playwright");
const fs = require("fs");
const path = require("path");
const { BASE, LS_URL, LS_TOKEN, LS_PROJECT, P1, P1_CODE, P2, P2_CODE, ADMIN_TOKEN, SHARE_ID, OUT = "test-results" } = process.env;
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(name, ok, extra = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`);
  if (!ok) failures++;
}
const shot = (page, name) => page.screenshot({ path: path.join(OUT, name) });

async function signIn(page, id, code) {
  await page.goto(BASE + "/");
  await page.fill("#study_id", id);
  await page.fill("#code", code);
  await page.click("button[type=submit]");
  await page.waitForURL("**/conversations");
}

async function dragSelect(page, msgIdx, target) {
  const r = await page.evaluate(([msgIdx, target]) => {
    const span = document.querySelectorAll('[class*="dialoguetext--"]')[msgIdx];
    span.scrollIntoView({ block: "center" });
    const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT);
    const nodes = []; let n, full = "";
    while ((n = walker.nextNode())) { nodes.push([n, full.length]); full += n.textContent; }
    const i = full.indexOf(target); if (i < 0) return null;
    const at = (pos) => { for (const [nd, off] of nodes) if (pos <= off + nd.textContent.length) return [nd, pos - off]; };
    const rg = document.createRange(); rg.setStart(...at(i)); rg.setEnd(...at(i + target.length));
    const rects = [...rg.getClientRects()].filter((x) => x.width > 0);
    const a = rects[0], z = rects[rects.length - 1];
    return { x1: a.left + 1, y1: a.top + a.height / 2, x2: z.right - 1, y2: z.top + z.height / 2 };
  }, [msgIdx, target]);
  if (!r) throw new Error("text not found: " + target);
  await page.mouse.move(r.x1, r.y1); await page.mouse.down();
  await page.mouse.move(r.x2, r.y2, { steps: 12 }); await page.mouse.up();
  await page.waitForTimeout(700);
}

// Highlights only (the editor also lists conversation-level answers as regions).
const regions = (page) => page.evaluate(() => (window.typologyEditor?.store?.annotationStore?.selected?.regions || [])
  .filter((r) => r.start !== undefined)
  .map((r) => ({ id: r.id, start: String(r.start), label: r.labeling?.selectedLabels?.[0]?.value ?? r.labels?.[0], text: r.text })));
const visible = (page, text) => page.getByText(text).first().isVisible().catch(() => false);

async function ls(pathname, opts = {}) {
  const res = await fetch(LS_URL + pathname, { ...opts, headers: { Authorization: `Token ${LS_TOKEN}`, ...(opts.headers || {}) } });
  return res.json();
}

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  // ---- sign in & import by share link ----
  await signIn(page, P1, P1_CODE);
  await shot(page, "1_conversations.png");
  await page.fill("#url", "https://evil.example/share/x");
  await page.click("#add-link");
  check("invalid link rejected with explanation", await page.getByText("Only ChatGPT share links").waitFor({ timeout: 5000 }).then(() => true, () => false));
  await page.fill("#url", `https://chatgpt.com/share/${SHARE_ID}`);
  await page.click("#add-link");
  await page.waitForURL("**/import/*");
  await page.waitForSelector("#review:not([hidden])", { timeout: 20000 });
  await shot(page, "2_preview.png");
  const previewCount = await page.locator("#messages li").count();
  check("preview shows extracted messages", previewCount === 4, `messages=${previewCount}`);
  const lsBefore = await ls(`/api/tasks/?project=${LS_PROJECT}`);
  check("nothing sent to Label Studio before confirmation", Array.isArray(lsBefore.tasks) && lsBefore.tasks.length === 0, JSON.stringify(lsBefore).slice(0, 120));
  await page.locator('#messages input[type="checkbox"]').nth(2).uncheck();
  check("excluding a message updates the count", (await page.textContent("#count")).startsWith("3 of 4"));
  await page.click("#confirm");
  await page.waitForURL("**/annotate/*");
  const convId = page.url().split("/").pop();
  await page.waitForSelector('[class*="phrase--"]', { timeout: 60000 });
  await page.waitForTimeout(1500);
  await shot(page, "3_annotate.png");

  const lsTasks = (await ls(`/api/tasks/?project=${LS_PROJECT}&fields=all`)).tasks;
  const task = lsTasks[0];
  check("Label Studio task created with data.dialogue", lsTasks.length === 1 && task.data.dialogue.length === 3 && task.data.conversation_id === convId);
  check("excluded message not in Label Studio", !JSON.stringify(task.data).includes("That changed my meaning"));
  check("Unicode text unchanged in Label Studio", task.data.dialogue[1].text.includes("谢谢, شكرا 👩🏽‍🦯") && task.data.dialogue[0].text.includes("🙂"));

  const bubble = await page.locator('[class*="phrase--"]').first().evaluate((e) => ({ bg: getComputedStyle(e).backgroundColor, ml: parseFloat(getComputedStyle(e).marginLeft) }));
  check("ChatGPT-style user bubble", bubble.bg === "rgb(244, 244, 244)" && bubble.ml > 0, JSON.stringify(bubble));

  // ---- annotate: labels chosen automatically from the message ----
  await page.locator('[class*="dialoguetext--"]').nth(1).click();
  check("clicking a message creates nothing", (await regions(page)).length === 0);
  await dragSelect(page, 0, "neurodivergent 🙂 to get");
  let rs = await regions(page);
  check("highlight in my message gets 'my text' automatically", rs.length === 1 && rs[0].label === "my text", JSON.stringify(rs));
  await dragSelect(page, 1, "谢谢, شكرا 👩🏽‍🦯");
  rs = await regions(page);
  check("highlight in AI message gets 'AI's text' automatically", rs.length === 2 && rs[1].label === "AI's text", JSON.stringify(rs));
  await dragSelect(page, 0, "🙂 to get the AI");
  rs = await regions(page);
  check("overlapping highlight", rs.length === 3);
  await dragSelect(page, 0, "less blunt?");
  await page.waitForTimeout(300);
  // a highlight dragged across two messages is removed
  const before = (await regions(page)).length;
  await page.evaluate(() => window.getSelection().removeAllRanges());
  const a = await page.locator('[class*="dialoguetext--"]').nth(0).boundingBox();
  const b = await page.locator('[class*="dialoguetext--"]').nth(1).boundingBox();
  await page.mouse.move(a.x + a.width - 20, a.y + a.height - 8); await page.mouse.down();
  await page.mouse.move(b.x + 40, b.y + 10, { steps: 15 }); await page.mouse.up();
  await page.waitForTimeout(800);
  check("highlight across two messages is not kept, with an explanation", (await regions(page)).length === before
    && (await page.textContent("#save-status")).includes("within a single message"), `before=${before} after=${(await regions(page)).length}`);

  await page.keyboard.press("Escape");
  await page.locator('[class*="dialoguetext--"]').nth(0).locator(".htx-highlight").first().click();
  await page.waitForTimeout(600);
  check("user highlight shows user questions only", await visible(page, "WHY: What were you trying") && !(await visible(page, "What did the AI do?")));
  await shot(page, "4_user_questions.png");
  await page.locator('textarea[name="user_why"]').fill("People at work call me blunt");
  await page.locator('textarea[name="user_why"]').press("Shift+Enter");
  await page.getByText("Adjusting tone or communication style").first().click();
  await page.keyboard.press("Escape");
  await page.locator('[class*="dialoguetext--"]').nth(1).locator(".htx-highlight").first().click();
  await page.waitForTimeout(600);
  check("AI highlight shows AI questions only", await visible(page, "What did the AI do?") && !(await visible(page, "WHY: What were you trying")));
  await shot(page, "5_ai_questions.png");
  await page.getByText("Modified my language or tone").first().click();
  await page.locator(".ant-checkbox-wrapper", { hasText: /^Undermining/ }).first().click();
  await page.keyboard.press("Escape");
  await page.locator('textarea[name="conversation_accessibility_context"]').fill("Writing to my manager");
  await page.locator('textarea[name="conversation_accessibility_context"]').press("Shift+Enter");

  await page.getByRole("button", { name: /submit/i }).first().click();
  check("save confirmed only after server accepts it",
    await page.getByText("Saved. You can keep editing").waitFor({ timeout: 15000 }).then(() => true, () => false),
    await page.textContent("#save-status"));

  // ---- reopen & edit ----
  await page.reload();
  await page.waitForSelector('[class*="phrase--"]'); await page.waitForTimeout(2000);
  rs = await regions(page);
  check("highlights restored after reload", rs.length === 4, `regions=${rs.length}`);
  await shot(page, "6_reopened.png");
  const lessBlunt = page.locator('[class*="dialoguetext--"]').nth(0).locator(".htx-highlight", { hasText: "less blunt?" }).first();
  await lessBlunt.scrollIntoViewIfNeeded();
  await lessBlunt.click();
  await page.keyboard.press("Backspace");
  await page.waitForTimeout(500);
  await page.getByText("Update", { exact: true }).first().click();
  await page.getByText("Saved. You can keep editing").waitFor({ timeout: 15000 }).catch(() => {});
  const lsAnn = await ls(`/api/tasks/${task.id}/annotations/`);
  const spans = lsAnn[0].result.filter((r) => r.from_name === "passage_type");
  check("edit (delete a highlight) saved to Label Studio", lsAnn.length === 1 && spans.length === 3, `annotations=${lsAnn.length} spans=${spans.length}`);

  // ---- second participant can't see the first participant's conversation ----
  const ctx2 = await browser.newContext();
  const page2 = await ctx2.newPage();
  await signIn(page2, P2, P2_CODE);
  check("other participant's list is empty", await page2.locator("#empty").isVisible());
  const res = await page2.request.get(`${BASE}/api/conversations/${convId}`);
  check("other participant gets 404 for the conversation", res.status() === 404 && !(await res.text()).includes("neurodivergent"));
  await page2.goto(`${BASE}/annotate/${convId}`);
  await page2.waitForTimeout(1500);
  check("other participant's annotate page shows no content", !(await page2.content()).includes("neurodivergent"));
  const lsDirect = await page2.request.get(`${LS_URL}/api/tasks/${task.id}/`);
  check("participant browser has no Label Studio access", lsDirect.status() === 401 || lsDirect.status() === 403, `status=${lsDirect.status()}`);
  await ctx2.close();

  // ---- research export ----
  const exp = await (await page.request.get(`${BASE}/admin/export`, { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } })).json();
  fs.writeFileSync(path.join(OUT, "export.json"), JSON.stringify(exp, null, 2));
  const conv = exp.conversations[0];
  const userSpan = conv.annotations.find((s) => s.span.text === "neurodivergent 🙂 to get");
  const aiSpan = conv.annotations.find((s) => s.message_role === "assistant");
  const msg = conv.messages[userSpan.message_index];
  check("export validation passes", conv.validation.ok && exp.summary.conversations_with_issues.length === 0, JSON.stringify(conv.validation));
  // span.start/end are Unicode code point positions (Python indexes); *_utf16 are JavaScript positions.
  check("export span maps to exact source text",
    Array.from(msg.text).slice(userSpan.span.start, userSpan.span.end).join("") === "neurodivergent 🙂 to get"
    && msg.text.slice(userSpan.span.start_utf16, userSpan.span.end_utf16) === "neurodivergent 🙂 to get");
  check("export answers attached to the right spans", userSpan.answers.user_why === "People at work call me blunt"
    && aiSpan.answers.ai_role?.includes("Undermining") && aiSpan.answers.ai_behavior?.includes("Modified my language or tone"));
  check("conversation-level answer exported once", conv.conversation_context.conversation_accessibility_context === "Writing to my manager");
  check("no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));

  await browser.close();
  console.log(failures ? `\n${failures} check(s) failed` : "\nAll checks passed");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
