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
  await page.waitForSelector(".dag-node");
  await editJson(page, "#run-input-json", JSON.stringify(input));
  await button(page, "Start run").click();
}

async function openAttempts(page, node) {
  await button(page, "Attempts for " + node).click();
  await page.waitForFunction(key => {
    const heading = document.activeElement;
    if (heading?.textContent !== "Attempts · " + key) return false;
    const rect = heading.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= innerHeight;
  }, {}, node);
}

async function checkAccessRefresh(page, ownerContext, home, workflow) {
  const membership = await open(ownerContext);
  const memberContext = await browser.createBrowserContext();
  try {
    const member = await open(memberContext);
    await membership.goto(origin + "/" + home + "/members");
    await login(member, accounts.developer, workflow);
    await member.bringToFront();
    const unsaved = JSON.stringify({ ...example, name: "Unsaved edits after access refresh" });
    await editJson(member, "#workflow-json", unsaved);
    const refresh = async () => {
      const response = member.waitForResponse(item =>
        item.request().method() === "GET" && item.url().endsWith("/api/v1" + home.slice(1)),
      );
      await button(member, "Refresh access").click();
      assert.equal((await response).status(), 200);
      await member.waitForSelector(".refresh-access:not(:disabled)");
      assert.equal(await member.$eval("#workflow-json", input => input.value), unsaved);
    };
    const client = await member.createCDPSession();
    try {
      client.on("Fetch.requestPaused", event => client.send("Fetch.failRequest", {
        requestId: event.requestId, errorReason: "Failed",
      }));
      await client.send("Fetch.enable", {
        patterns: [{ urlPattern: "*/api/v1" + home.slice(1), requestStage: "Request" }],
      });
      await button(member, "Refresh access").click();
      await result(member, "Could not reach FlowForge");
      assert.equal(await member.$eval("#workflow-json", input => input.value), unsaved);
    } finally {
      await client.detach();
    }
    await refresh();
    await text(member, ".muted", "Unsaved edits");

    await membership.bringToFront();
    await membership.select('select[aria-label="Role for ' + accounts.developer.email + '"]', "VIEWER");
    await result(membership, "Role updated.");
    await member.bringToFront();
    await refresh();
    await member.waitForSelector("#workflow-json[readonly]");
    assert.equal(await member.$$eval("button", buttons => buttons.some(item =>
      item.textContent === "Save draft")), false);

    await membership.bringToFront();
    await button(membership, "Remove " + accounts.developer.email).click();
    await button(membership, "Remove member").click();
    await result(membership, "Member removed.");
    await member.bringToFront();
    await button(member, "Refresh access").click();
    await result(member, "Project not found");
    assert.equal(await member.$("#workflow-json"), null);
    await membership.bringToFront();
    await addMember(membership, accounts.developer.email, "DEVELOPER");
  } finally {
    await memberContext.close();
    await membership.close();
    await page.bringToFront();
  }
  console.log("PASS: access refresh preserves draft edits and handles role downgrade and revocation");
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

async function dropRunResponses(page, count, action) {
  const client = await page.createCDPSession();
  let lost = 0;
  try {
    client.on("Fetch.requestPaused", async event => {
      if (lost < count && event.request.method === "POST" && event.responseStatusCode === 202) {
        lost++;
        await client.send("Fetch.failRequest", { requestId: event.requestId, errorReason: "Failed" });
      } else {
        await client.send("Fetch.continueRequest", { requestId: event.requestId });
      }
    });
    await client.send("Fetch.enable", {
      patterns: [{ urlPattern: "*/api/v1/workflows/*/runs", requestStage: "Response" }],
    });
    await action();
    assert.equal(lost, count);
  } finally {
    await client.detach();
  }
}

async function retryRunAfterLostResponse(page) {
  const requests = [];
  const watch = request => {
    if (request.method() === "POST" && request.url().endsWith("/runs")) {
      requests.push({ key: request.headers()["idempotency-key"], body: request.postData() });
    }
  };
  page.on("request", watch);
  try {
    await dropRunResponses(page, 2, async () => {
      await start(page);
      await result(page, "Run creation could not be confirmed");
      await button(page, "Retry run request").click();
      await result(page, "Run creation could not be confirmed");
      await link(page, "Check run history").click();
      await text(page, "h1", "Runs");
      await page.waitForSelector("tbody a");
      assert.equal(await page.$$eval("tbody tr", rows => rows.length), 1);
      const original = await page.$eval("tbody a", item => item.hash);
      await page.goBack();
      await page.waitForSelector("#run-input-json[readonly]");
      assert.deepEqual(JSON.parse(await page.$eval("#run-input-json", item => item.value)), input);
      await page.reload();
      await text(page, ".run-form h3", "version 1");
      await button(page, "Retry run request").click();
      await text(page, "h2", "Tasks");
      assert.equal(new URL(page.url()).hash, original);
    });
    assert.equal(requests.length, 3);
    assert(requests[0].key);
    for (const request of requests.slice(1)) assert.deepEqual(request, requests[0]);
  } finally {
    page.off("request", watch);
  }
  console.log("PASS: direct retry, history and reload preserve the original run key and payload");
}

async function checkWorkflowPagination(page, home) {
  const loginResponse = await fetch(origin + "/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FlowForge-CSRF": "1" },
    body: JSON.stringify(accounts.owner),
  });
  assert.equal(loginResponse.status, 200);
  const { access_token: accessToken } = await loginResponse.json();
  const listing = origin + "/api/v1" + home.slice(1) + "/workflows";
  for (let total = 2; total <= 40; total++) {
    const create = await fetch(listing, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: "Bearer " + accessToken },
      body: JSON.stringify({ name: "Pagination workflow " + total }),
    });
    assert.equal(create.status, 201);
    if (![19, 20, 21, 40].includes(total)) continue;
    await page.goto(origin + "/" + home);
    await page.reload();
    await page.waitForFunction(count => document.querySelectorAll("tbody tr").length === count,
      {}, Math.min(20, total));
    assert.equal(await page.$eval(".pager button:last-child", item => item.disabled), total <= 20);
    if (total <= 20) continue;
    const first = await page.$$eval("tbody a", items => items.map(item => item.hash));
    await button(page, "Next").click();
    await text(page, ".pager", "Page 2");
    const second = await page.$$eval("tbody a", items => items.map(item => item.hash));
    assert.equal(second.length, total - 20);
    assert.equal(new Set([...first, ...second]).size, total);
    assert.equal(await page.$eval(".pager button:last-child", item => item.disabled), true);
    await button(page, "Previous").click();
    await text(page, ".pager", "Page 1");
  }
  console.log("PASS: pagination at 19, 20, 21 and 40 workflows");
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
  const savedJson = await page.$eval("#workflow-json", input => input.value);
  await editJson(page, "#workflow-json", "{");
  await button(page, "Save draft").click();
  await result(page, "Enter a valid JSON object.");
  await page.focus("details summary");
  await page.keyboard.press("Enter");
  await button(page, "Use saved JSON").click();
  assert.equal(await page.$eval("#workflow-json", input => input.value), savedJson);
  assert.equal(await page.$(".notice.error"), null);
  await checkAccessRefresh(page, ownerContext, home, workflow);
  await checkRefreshAndConflict(page, ownerContext, workflow);
  await publish(page, 1);
  await save(page, { ...example, edges: [...example.edges, ["report", "validate"]] });
  await button(page, "Publish version").click();
  await result(page, "workflow contains a cycle");
  assert.equal(await page.$$eval("tbody tr", rows => rows.length), 1);
  await save(page, { ...example, nodes: example.nodes.slice(0, 1), edges: [] });
  await publish(page, 2);
  await button(page, "View version 1").click();
  await text(page, "h2", "Published version 1");
  await retryRunAfterLostResponse(page);
  const run = new URL(page.url()).hash;
  await text(page, '[role="status"]', "SUCCEEDED", 60000);
  assert.equal(await page.$$eval(".dag-node", nodes => nodes.length), example.nodes.length);
  for (const { key } of example.nodes) {
    await openAttempts(page, key);
    await text(page, "#attempt-history tbody td", "#1");
  }
  await page.goto(origin + "/" + workflow);
  await button(page, "View version 1").click();
  await text(page, "h2", "Published version 1");
  await button(page, "Start run").click();
  await result(page, "Run input does not resolve");
  assert.equal(await page.$("#run-input-json[readonly]"), null);
  const freshRequest = page.waitForResponse(response =>
    response.request().method() === "POST" && response.url().endsWith("/runs"),
  );
  await start(page);
  assert.equal((await freshRequest).status(), 202);
  await text(page, "h2", "Tasks");
  assert.notEqual(new URL(page.url()).hash, run);

  const viewerContext = await browser.createBrowserContext();
  try {
    const viewer = await open(viewerContext);
    await viewer.bringToFront();
    await login(viewer, accounts.viewer, run);
    await text(viewer, '[role="status"]', "SUCCEEDED");
    await openAttempts(viewer, "validate");
    await text(viewer, "#attempt-history tbody td", "#1");
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
  assert.equal(await page.$$eval("tbody tr", rows => rows.length), 2);
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
  await openAttempts(page, "report");
  await text(page, "#attempt-history tbody td", "#1");
  await openAttempts(page, "report");
  await page.setViewport({ width: 1280, height: 900 });
  await checkWorkflowPagination(page, home);
  await page.goto(origin + "/" + workflow);
  await dropRunResponses(page, 1, async () => {
    await start(page);
    await result(page, "Run creation could not be confirmed");
  });
  await button(page, "Sign out").click();
  await text(page, "h2", "Sign in");
  for (const account of [accounts.developer, accounts.owner]) {
    await login(page, account, workflow);
    await page.waitForSelector("#run-input-json");
    assert.equal(await page.$eval("#run-input-json", item => item.value), "{}");
    await button(page, "Sign out").click();
    await text(page, "h2", "Sign in");
  }
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
  await button(page, "Keep").click();
  assert.equal(await page.$(".confirm"), null);
  await button(page, "Cancel run").click();
  await button(page, "Confirm cancellation").click();
  await result(page, "Run status: CANCELED.");
  await text(page, "tbody td", "CANCELED");
  await openAttempts(page, "validate");
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
