// End-to-end participant flow against a running app.
// BASE=http://localhost:8765 STUDY_ID=P01 CODE=... EXPORT_ZIP=path ADMIN_TOKEN=... OUT=dir node tests/e2e/flow.cjs
const { chromium } = require(process.env.PLAYWRIGHT_PATH || "playwright");
const fs = require("fs");
const path = require("path");
const { BASE = "http://localhost:8765", STUDY_ID, CODE, EXPORT_ZIP, ADMIN_TOKEN, OUT = "test-results" } = process.env;
fs.mkdirSync(OUT, { recursive: true });

let failures = 0;
function check(name, ok, extra = "") {
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${extra ? "  " + extra : ""}`);
  if (!ok) failures++;
}

async function highlight(page, msgIdx, target) {
  const r = await page.evaluate(([msgIdx, target]) => {
    const span = document.querySelectorAll('[class*="dialoguetext--"]')[msgIdx];
    const walker = document.createTreeWalker(span, NodeFilter.SHOW_TEXT);
    const nodes = []; let n, full = "";
    while ((n = walker.nextNode())) { nodes.push([n, full.length]); full += n.textContent; }
    const i = full.indexOf(target); if (i < 0) return null;
    const at = (pos) => { for (const [nd, off] of nodes) if (pos <= off + nd.textContent.length) return [nd, pos - off]; };
    const rg = document.createRange(); rg.setStart(...at(i)); rg.setEnd(...at(i + target.length));
    const rects = [...rg.getClientRects()].filter((x) => x.width > 0); const a = rects[0], z = rects[rects.length - 1];
    a && span.scrollIntoView({ block: "center" });
    const rr = [...rg.getClientRects()].filter((x) => x.width > 0);
    return { x1: rr[0].left + 1, y1: rr[0].top + rr[0].height / 2, x2: rr[rr.length - 1].right - 1, y2: rr[rr.length - 1].top + rr[rr.length - 1].height / 2 };
  }, [msgIdx, target]);
  if (!r) throw new Error("text not found: " + target);
  await page.mouse.move(r.x1, r.y1); await page.mouse.down(); await page.mouse.move(r.x2, r.y2, { steps: 12 }); await page.mouse.up();
  await page.waitForTimeout(600);
}
const highlightCount = (page) => page.locator('[class*="dialoguetext--"] .htx-highlight').count();
const visible = (page, text) => page.getByText(text).first().isVisible().catch(() => false);

(async () => {
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 1000 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));

  await page.goto(BASE + "/");
  await page.fill("#study_id", STUDY_ID); await page.fill("#code", "wrong-code"); await page.click("button[type=submit]");
  check("wrong access code is rejected", await page.getByText("incorrect").waitFor({ timeout: 5000 }).then(() => true, () => false));
  await page.fill("#code", CODE); await page.click("button[type=submit]");
  await page.waitForURL("**/conversations");
  check("sign in", true);
  await page.screenshot({ path: path.join(OUT, "1_conversations.png"), fullPage: true });

  await page.setInputFiles("#file", EXPORT_ZIP);
  await page.waitForSelector('input[name="conv"]');
  check("export zip read in browser", (await page.locator('input[name="conv"]').count()) >= 1);
  await page.screenshot({ path: path.join(OUT, "2_pick_conversation.png"), fullPage: true });
  await page.locator('input[name="conv"]').first().check();
  await page.click("#add");
  await page.waitForURL("**/annotate/*");
  await page.waitForSelector('[class*="phrase--"]', { timeout: 60000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT, "3_annotate.png") });

  const bubble = await page.locator('[class*="phrase--"]').first().evaluate((e) => ({ bg: getComputedStyle(e).backgroundColor, ml: parseFloat(getComputedStyle(e).marginLeft) }));
  check("user message styled as right-aligned bubble", bubble.bg === "rgb(244, 244, 244)" && bubble.ml > 0, JSON.stringify(bubble));

  await page.locator('[class*="dialoguetext--"]').nth(1).click();
  check("clicking a message creates nothing", (await highlightCount(page)) === 0);

  await page.keyboard.press("1"); await page.waitForTimeout(200);
  await highlight(page, 0, "I come across as blunt");
  check("highlight in user message", (await highlightCount(page)) === 1);
  await page.keyboard.press("Escape"); await page.keyboard.press("1"); await page.waitForTimeout(200);
  await highlight(page, 0, "Can you make it sound more professional");
  await page.keyboard.press("Escape"); await page.keyboard.press("1"); await page.waitForTimeout(200);
  await highlight(page, 0, "more professional without changing");
  check("second and overlapping highlights", (await highlightCount(page)) >= 3, `highlights=${await highlightCount(page)}`);
  await page.keyboard.press("Escape"); await page.keyboard.press("2"); await page.waitForTimeout(200);
  await highlight(page, 1, "I hope you're doing well!");
  await page.keyboard.press("Escape");

  await page.locator('[class*="dialoguetext--"]').nth(0).locator(".htx-highlight").first().click();
  await page.waitForTimeout(600);
  check("user highlight opens user questions", await visible(page, "WHY: What were you trying"));
  check("user highlight hides AI questions", !(await visible(page, "What did the AI do?")));
  await page.screenshot({ path: path.join(OUT, "4_user_questions.png") });
  await page.locator('textarea[name="user_why"]').fill("Professional expectations; people call me blunt");
  await page.locator('textarea[name="user_why"]').press("Shift+Enter");
  await page.getByText("Adjusting tone or communication style").first().click();
  await page.keyboard.press("Escape");

  await page.locator('[class*="dialoguetext--"]').nth(1).locator(".htx-highlight").first().click();
  await page.waitForTimeout(600);
  check("AI highlight opens AI questions", await visible(page, "What did the AI do?"));
  check("AI highlight hides user questions", !(await visible(page, "WHY: What were you trying")));
  await page.screenshot({ path: path.join(OUT, "5_ai_questions.png") });
  await page.getByText("Modified my language or tone").first().click();
  await page.keyboard.press("Escape");

  await page.locator('textarea[name="conversation_accessibility_context"]').fill("Help writing to my manager");
  await page.locator('textarea[name="conversation_accessibility_context"]').press("Shift+Enter");

  await page.getByRole("button", { name: /submit/i }).first().click();
  await page.getByText("Submitted").waitFor({ timeout: 10000 });
  check("submit saves to the app", true);

  await page.reload();
  await page.waitForSelector('[class*="phrase--"]'); await page.waitForTimeout(2000);
  check("highlights persist after reload", (await highlightCount(page)) >= 4, `highlights=${await highlightCount(page)}`);
  await page.screenshot({ path: path.join(OUT, "6_after_reload.png") });

  const res = await page.request.get(BASE + "/admin/export", { headers: { Authorization: `Bearer ${ADMIN_TOKEN}` } });
  const exp = await res.json();
  fs.writeFileSync(path.join(OUT, "export.json"), JSON.stringify(exp, null, 2));
  const conv = exp.conversations[exp.conversations.length - 1];
  const blunt = conv.spans.find((s) => s.selected_text === "I come across as blunt");
  check("export has span with message id, role, offsets, answers",
    blunt && blunt.message_id === "msg-1" && blunt.role === "user" && blunt.answers.user_why?.[0]?.startsWith("Professional")
    && conv.messages[0].text.slice(blunt.start_char, blunt.end_char) === "I come across as blunt", JSON.stringify(blunt));
  check("export has AI span", conv.spans.some((s) => s.role === "assistant" && s.answers.ai_behavior?.includes("Modified my language or tone")));
  check("conversation-level answer stored once", conv.conversation_context.conversation_accessibility_context?.[0] === "Help writing to my manager");
  check("no page errors", errors.length === 0, errors.slice(0, 3).join(" | "));

  await browser.close();
  console.log(failures ? `\n${failures} check(s) failed` : "\nAll checks passed");
  process.exit(failures ? 1 : 0);
})().catch((e) => { console.error(e); process.exit(1); });
