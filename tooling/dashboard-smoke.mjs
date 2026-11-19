import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import puppeteer from "puppeteer";

const origin = "http://127.0.0.1:5173";
const example = JSON.parse(await readFile(new URL("./document-workflow.json", import.meta.url), "utf8"));
const input = { document: { title: "Dashboard report", content: "FlowForge dashboard smoke." } };
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const accounts = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const phase = process.argv[2];
const browser = await puppeteer.launch();

const control = (page, name, role) =>
  page.locator('::-p-aria([name="' + name + '"][role="' + role + '"])');
const button = (page, name) => control(page, name, "button");
const field = (page, name) => control(page, name, "textbox");
const link = (page, name) => control(page, name, "link");
const text = (page, selector, value, timeout = 15000) => page.waitForFunction(
  (query, expected) => [...document.querySelectorAll(query)].some(
    node => node.textContent.includes(expected),
  ),
  { timeout }, selector, value,
);
const result = (page, value) => text(page, '.notice, [role="status"]', value);

async function open(context) {
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  return page;
}

async function login(page, account, route = "#/") {
  await page.goto(origin + "/" + route, { waitUntil: "domcontentloaded" });
  await field(page, "Email").fill(account.email);
  await page.locator('input[type="password"]').fill(account.password);
  await button(page, "Sign in").click();
  await page.waitForSelector(".account");
  assert(await page.evaluate(() => isSecureContext && Boolean(navigator.locks)));
}

async function createProject(page) {
  await login(page, accounts.owner);
  await field(page, "Project name").fill("Dashboard smoke");
  await button(page, "Create project").click();
  await text(page, "h1", "Workflows");
  return new URL(page.url()).hash;
}

async function addMember(page, email, role) {
  await field(page, "Email").fill(email);
  await page.select(".form-row select", role);
  await button(page, "Add member").click();
  await result(page, "Member added.");
  await text(page, "tbody td", email);
}

async function createWorkflow(page, home) {
  await page.goto(origin + "/" + home);
  await field(page, "Workflow name").fill("Document workflow smoke");
  await button(page, "Create workflow").click();
  await text(page, "h1", "Document workflow smoke");
  await page.waitForSelector("textarea");
  return new URL(page.url()).hash;
}

async function editJson(page, selector, value) {
  await page.locator(selector).click();
  await page.keyboard.down("Control");
  try {
    await page.keyboard.press("KeyA");
  } finally {
    await page.keyboard.up("Control");
  }
  const client = await page.createCDPSession();
  try {
    await client.send("Input.insertText", { text: value });
  } finally {
    await client.detach();
  }
  assert.equal(await page.$eval(selector, element => element.value), value);
}

async function save(page, dag) {
  await editJson(page, "#workflow-json", JSON.stringify(dag, null, 2));
  await text(page, ".muted", "Unsaved edits");
  await button(page, "Save draft").click();
  await result(page, "Draft saved.");
}

async function publish(page, version) {
  await button(page, "Publish version").click();
  await result(page, "Published version " + version + ".");
  await text(page, "h2", "Published version " + version);
}

async function start(page) {
  await editJson(page, "#run-input-json", JSON.stringify(input));
  await button(page, "Start run").click();
}

async function checkRefreshAndConflict(page, context, workflow) {
  const other = await open(context);
  try {
    await other.goto(origin + "/" + workflow);
    await other.waitForSelector("textarea");
    const inFlight = new Set();
    let started = 0, peak = 0;
    for (const tab of [page, other]) {
      tab.on("request", request => {
        if (request.url().endsWith("/api/v1/auth/refresh")) {
          started++;
          inFlight.add(request);
          peak = Math.max(peak, inFlight.size);
        }
      });
      tab.on("requestfinished", request => inFlight.delete(request));
      tab.on("requestfailed", request => inFlight.delete(request));
    }
    await Promise.all([page.reload(), other.reload()]);
    await Promise.all([page.waitForSelector("textarea"), other.waitForSelector("textarea")]);
    assert.equal(started, 2);
    assert.equal(peak, 1);
    await page.bringToFront();
    await save(page, example);
    await other.bringToFront();
    const edit = JSON.stringify({ ...example, name: "Conflicting browser draft" });
    await editJson(other, "#workflow-json", edit);
    await button(other, "Save draft").click();
    await result(other, "draft changed elsewhere");
    await button(other, "Load current revision").click();
    await result(other, "Current revision loaded");
    assert.equal(await other.$eval("textarea", element => element.value), edit);
    await text(other, ".muted", "Revision 2");
  } finally {
    await other.close();
    await page.bringToFront();
  }
  console.log("PASS: two-tab refresh serialization and draft conflict recovery");
}

async function retryRunAfterLostResponse(page) {
  const requests = [];
  const watch = request => {
    if (request.method() === "POST" && request.url().endsWith("/runs")) {
      requests.push({ key: request.headers()["idempotency-key"], body: request.postData() });
    }
  };
  page.on("request", watch);
  const client = await page.createCDPSession();
  let lost = false;
  try {
    client.on("Fetch.requestPaused", async event => {
      if (!lost && event.request.method === "POST" && event.responseStatusCode === 202) {
        lost = true;
        await client.send("Fetch.failRequest", { requestId: event.requestId, errorReason: "Failed" });
      } else {
        await client.send("Fetch.continueRequest", { requestId: event.requestId });
      }
    });
    await client.send("Fetch.enable", {
      patterns: [{ urlPattern: "*/api/v1/workflows/*/runs", requestStage: "Response" }],
    });
    await start(page);
    await result(page, "Run creation could not be confirmed");
    assert.equal(lost, true);
    await client.send("Fetch.disable");
    await button(page, "Retry run request").click();
    await text(page, "h2", "Tasks");
    assert.equal(requests.length, 2);
    assert(requests[0].key);
    assert.deepEqual(requests[0], requests[1]);
  } finally {
    page.off("request", watch);
    await client.detach();
  }
  console.log("PASS: lost run response retried with the same key and payload");
}

async function full(page, ownerContext) {
  const home = await createProject(page);
  await link(page, "Members").click();
  await text(page, "h1", "Members");
  await addMember(page, accounts.developer.email, "VIEWER");
  await page.select('select[aria-label="Role for ' + accounts.developer.email + '"]', "DEVELOPER");
  await result(page, "Role updated.");
  await page.waitForFunction(email => document.querySelector('select[aria-label="Role for ' + email + '"]')?.value === "DEVELOPER",
    {}, accounts.developer.email);
  await addMember(page, accounts.viewer.email, "VIEWER");
  console.log("PASS: owner added members and updated project roles");
  const workflow = await createWorkflow(page, home);
  await checkRefreshAndConflict(page, ownerContext, workflow);
  await publish(page, 1);
  await save(page, { ...example, nodes: example.nodes.slice(0, 1), edges: [] });
  await publish(page, 2);
  await button(page, "View version 1").click();
  await text(page, "h2", "Published version 1");
  await retryRunAfterLostResponse(page);
  const run = new URL(page.url()).hash;
  await text(page, '[role="status"]', "SUCCEEDED", 60000);
  assert.equal(await page.$$eval(".dag-node", nodes => nodes.length), example.nodes.length);
  await button(page, "Attempts for validate").click();
  await text(page, "h2", "Attempts · validate");
  await text(page, "tbody td", "#1");

  const viewerContext = await browser.createBrowserContext();
  try {
    const viewer = await open(viewerContext);
    await viewer.bringToFront();
    await login(viewer, accounts.viewer, run);
    await text(viewer, '[role="status"]', "SUCCEEDED");
    await button(viewer, "Attempts for validate").click();
    await text(viewer, "tbody td", "#1");
    await viewer.goto(origin + "/" + workflow);
    await viewer.waitForSelector("textarea[readonly]");
    assert.equal(await viewer.$$eval("button", buttons => buttons.some(item =>
      ["Save draft", "Publish version", "Start run"].includes(item.textContent))), false);
    await viewer.goto(origin + "/" + home + "/members");
    await result(viewer, "Only the project owner");
    await page.bringToFront();
    await page.goto(origin + "/" + home + "/members");
    await button(page, "Remove " + accounts.viewer.email).click();
    await button(page, "Remove member").click();
    await result(page, "Member removed.");
    await viewer.bringToFront();
    await viewer.reload();
    await result(viewer, "Project not found");
  } finally {
    await viewerContext.close();
    await page.bringToFront();
  }

  await page.goto(origin + "/" + home + "/runs?workflow=" + workflow.split("/").at(-1));
  await page.waitForSelector("tbody td");
  assert.equal(await page.$$eval("tbody tr", rows => rows.length), 1);
  await page.setViewport({ width: 390, height: 844 });
  await page.goto(origin + "/" + run);
  await page.reload();
  await text(page, '[role="status"]', "SUCCEEDED");
  await page.waitForSelector(".dag-node");
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await page.keyboard.press("Tab");
  assert(await page.evaluate(() => document.activeElement?.textContent === "Skip to content"));
  await page.keyboard.press("Enter");
  assert(await page.evaluate(() => document.activeElement?.id === "dashboard-content"));
  await button(page, "Sign out").click();
  await text(page, "h2", "Sign in");
  console.log("PASS: project membership, versioned DAG, run history, attempts, keyboard and mobile");
}

async function cancellation(page) {
  const home = await createProject(page);
  await createWorkflow(page, home);
  await save(page, example);
  await publish(page, 1);
  await start(page);
  await text(page, '[role="status"]', "PENDING");
  await button(page, "Cancel run").click();
  await button(page, "Confirm cancellation").click();
  await result(page, "Run status: CANCELED.");
  await text(page, "tbody td", "CANCELED");
  await button(page, "Attempts for validate").click();
  await text(page, ".empty", "no attempts yet");
  console.log("PASS: browser cancellation preserves zero attempts for an unstarted run");
}

try {
  const context = await browser.createBrowserContext();
  const page = await open(context);
  const errors = [];
  browser.on("targetcreated", target => {
    if (target.type() === "page") void target.page().then(tab =>
      tab?.on("pageerror", error => errors.push(error.message)));
  });
  page.on("pageerror", error => errors.push(error.message));
  if (phase === "full") await full(page, context);
  else if (phase === "cancel") await cancellation(page);
  else throw new Error("Unknown dashboard smoke phase");
  assert.deepEqual(errors, []);
} finally {
  await browser.close();
}
