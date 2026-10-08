import { test, expect, type Page, type Route } from "@playwright/test";
import type { ApprovalView, RunOut } from "../../lib/contracts";

const now = "2026-10-08T04:00:00Z";
const headers = {
  "access-control-allow-origin": "http://127.0.0.1:3001",
  "access-control-allow-headers": "authorization,content-type",
  "access-control-allow-methods": "GET,POST,OPTIONS",
};
const desktopStatus = {
  protocol_version: 1, state: "disconnected", paired: false,
  machine_id: null, session_id: null, platform_support: ["posix"],
  reason: "No user-reviewed desktop session. This server cannot access a user’s computer.",
  capabilities: ["files.list", "files.read_text", "files.search_text", "browser.control", "desktop.rpa", "shell.execute"].map((name) => ({ name, implemented: name.startsWith("files."), available: false })),
};
const makeRun = (patch: Partial<RunOut> = {}): RunOut => ({
  id: "saved-run", agent_id: "manager", status: "paused", input_text: "Continue safely",
  created_at: now, completed_at: null, can_resume: true, can_cancel: true,
  resume_blocked_reason: null, cancellation_requested: false, executing_tool_ids: [], uncertain_tool_ids: [],
  events: [{ sequence: 1, type: "run.paused", data: {}, created_at: now }], ...patch,
});
const completed = (run: RunOut, answer = "Continued from the saved tool result."): RunOut => ({ ...run, status: "completed", can_resume: false, can_cancel: false, completed_at: now, events: [...run.events, { sequence: 2, type: "run.completed", data: { answer }, created_at: now }] });
function latch() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => { release = resolve; });
  return { promise, release };
}
type Handler = (route: Route, path: string) => Promise<boolean>;
async function setup(page: Page, handler: Handler, approvals: () => ApprovalView[] = () => []) {
  const agent = { id: "manager", name: "JARVIS", role: "Manager", description: "Your manager", mode: "personal", capabilities: ["conversation"], tools_enabled: true, available: true };
  await page.addInitScript(() => { sessionStorage.setItem("jarvis_access_token", "continuation-fixture"); sessionStorage.setItem("jarvis_refresh_token", "continuation-refresh"); });
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "OPTIONS") { await route.fulfill({ status: 204, headers }); return; }
    if (await handler(route, path)) return;
    const values: Record<string, unknown> = {
      "/me": { id: "fixture", email: "fixture@example.invalid", display_name: "Test User", scopes: ["chat"], preferred_language: "en", created_at: now },
      "/models": { allow_cloud: false, providers: [{ name: "ollama", privacy: "local", default_model: "fixture", configured: true }] },
      "/agents": { agents: [agent], execution: { mode: "inline", durable_queue: false, approval_resume: true } },
      "/projects": [], "/runs": [], "/desktop/status": desktopStatus,
      "/api/v1/office/snapshot": { generatedAt: now, agents: [{ ...agent, status: "idle" }], tasks: [], events: [], approvals: approvals() },
    };
    await route.fulfill({ headers, json: values[path] ?? {} });
  });
}
async function openRun(page: Page) {
  await page.goto("/#runs");
  await page.getByRole("button", { name: /^Continue safely/ }).click();
}
async function runRead(route: Route, path: string, run: RunOut) {
  if (path === "/runs") { await route.fulfill({ headers, json: [{ ...run, input_preview: run.input_text }] }); return true; }
  if (path === `/runs/${run.id}`) { await route.fulfill({ headers, json: run }); return true; }
  return false;
}

test("resume is single-flight across navigation and updates the saved response", async ({ page }) => {
  let run = makeRun(); let resumes = 0;
  const gate = latch();
  await setup(page, async (route, path) => {
    if (path.endsWith("/resume")) {
      resumes++; await gate.promise; run = completed(run);
      await route.fulfill({ headers, json: run }); return true;
    }
    return runRead(route, path, run);
  });
  await openRun(page);
  await page.getByRole("button", { name: "Resume run", exact: true }).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(page.getByRole("button", { name: "Resuming…", exact: true })).toBeDisabled();
  await page.getByRole("link", { name: "AI team", exact: true }).click();
  await page.getByRole("link", { name: "Tasks & runs", exact: true }).click();
  await page.getByRole("button", { name: /^Continue safely/ }).click();
  await expect(page.getByRole("button", { name: "Resuming…", exact: true })).toBeDisabled();
  expect(resumes).toBe(1); gate.release();
  await expect(page.getByText("Continued from the saved tool result.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume run", exact: true })).toHaveCount(0);
  await page.screenshot({ path: "test-results/run-resumed-desktop.png", fullPage: true });
});

test("cancellation during resume wins over its late response without replay", async ({ page }) => {
  let run = makeRun(); let resumes = 0; let cancels = 0;
  const gate = latch(); const staleCompleted = completed(run, "STALE RESUME RESULT");
  await setup(page, async (route, path) => {
    if (path.endsWith("/resume")) { resumes++; await gate.promise; await route.fulfill({ headers, json: staleCompleted }); return true; }
    if (path.endsWith("/cancel")) { cancels++; run = { ...run, status: "cancelled", cancellation_requested: true, can_resume: false, can_cancel: false, executing_tool_ids: ["in-flight-tool"] }; await route.fulfill({ headers, json: run }); return true; }
    return runRead(route, path, run);
  });
  await openRun(page);
  await page.getByRole("button", { name: "Resume run", exact: true }).click();
  await expect.poll(() => resumes).toBe(1);
  await page.getByRole("button", { name: "Cancel run", exact: true }).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect(page.getByText(/Cancellation requested. A tool is still executing/)).toBeVisible();
  await expect(page.getByText(/cannot undo or guarantee stopping/)).toBeVisible();
  gate.release();
  await expect(page.getByText(/earlier resume request is still finishing/)).toHaveCount(0);
  await expect(page.getByText("STALE RESUME RESULT", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Continue safely.*cancelled/ })).toBeVisible();
  expect(resumes).toBe(1); expect(cancels).toBe(1);
});

test("uncertain effects block resume and cancellation never claims to undo them", async ({ page }) => {
  let run = makeRun({ can_resume: false, uncertain_tool_ids: ["unknown-write"], resume_blocked_reason: "A tool outcome is uncertain; automatic retry is blocked." });
  let resumes = 0;
  await setup(page, async (route, path) => {
    if (path.endsWith("/resume")) { resumes++; await route.fulfill({ status: 409, headers, json: { detail: "Uncertain tool outcome" } }); return true; }
    if (path.endsWith("/cancel")) { run = { ...run, status: "cancelled", cancellation_requested: true, can_cancel: false }; await route.fulfill({ headers, json: run }); return true; }
    return runRead(route, path, run);
  });
  await openRun(page);
  await expect(page.getByRole("button", { name: "Resume run", exact: true })).toBeDisabled();
  await expect(page.getByText(/An effect may already have happened/)).toBeVisible();
  await page.getByRole("button", { name: "Cancel run", exact: true }).click();
  await expect(page.getByText(/Completed effects are not reversed/)).toBeVisible();
  await expect(page.getByText(/Nothing will be retried automatically/)).toBeVisible();
  expect(resumes).toBe(0);
});

test("unconfirmed action is not retried and requires explicit saved-status recovery", async ({ page }) => {
  let run = makeRun(); let resumes = 0;
  await setup(page, async (route, path) => {
    if (path.endsWith("/resume")) {
      resumes++; run = completed(run, "Server finished despite the lost response.");
      await route.abort("failed"); return true;
    }
    return runRead(route, path, run);
  });
  await openRun(page); await page.getByRole("button", { name: "Resume run", exact: true }).click();
  // Next's global route announcer also has role=alert; inspect this run's error.
  const runHistory = page.getByRole("region").filter({ has: page.getByRole("heading", { name: "Run history", exact: true }) });
  await expect(runHistory.getByRole("alert")).toContainText("Refresh saved status before trying again");
  expect(resumes).toBe(1);
  await page.getByRole("button", { name: "Refresh saved status", exact: true }).click();
  await expect(page.getByText("Server finished despite the lost response.", { exact: true })).toBeVisible();
  expect(resumes).toBe(1);
});

test("approval continues the original conversation while new actions require fresh review", async ({ page }) => {
  let run = makeRun({ status: "awaiting_approval", can_resume: false, resume_blocked_reason: "Pending tool approval" });
  let stage = 0; let decisions = 0;
  const gate = latch();
  const approval = (id: string): ApprovalView => ({ id, toolName: "write_file", risk: "write", args: { path: `/workspace/${id}.txt`, content: "Reviewed fixture content" }, requestedAt: now });
  await setup(page, async (route, path) => {
    if (path === "/runs/stream") {
      await route.fulfill({ headers: { ...headers, "content-type": "text/event-stream" }, body: `event: run.started\ndata: {"sequence":1,"data":{"run_id":"saved-run","agent_id":"manager"}}\n\nevent: end\ndata: {"status":"awaiting_approval","run_id":"saved-run"}\n\n` }); return true;
    }
    if (path === "/tools/first/approve") {
      decisions++; await gate.promise; stage = 1;
      await route.fulfill({ headers, json: { id: "first", status: "completed", run_id: run.id, run_status: "awaiting_approval" } }); return true;
    }
    if (path === "/tools/second/deny") {
      decisions++; stage = 2; run = completed(run, "Finished after respecting your denied second action.");
      await route.fulfill({ headers, json: { id: "second", status: "denied", run_id: run.id, run_status: "completed" } }); return true;
    }
    return runRead(route, path, run);
  }, () => stage < 2 ? [approval(stage === 0 ? "first" : "second")] : []);
  await page.goto("/");
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Continue safely");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await page.getByRole("button", { name: "Review approval", exact: false }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Approve & run", exact: true }).click();
  await expect.poll(() => decisions).toBe(1);
  const navigation = page.getByRole("navigation", { name: "Main navigation", exact: true });
  const workspaceLink = navigation.getByRole("link", { name: "My workspace", exact: true });
  await workspaceLink.click();
  await expect(page).toHaveURL(/#workspace$/);
  await expect(workspaceLink).toHaveAttribute("aria-current", "page");
  // This fixture still has one pending action. The badge is part of the link's
  // accessible name, so assert it exactly rather than ignoring pending state.
  const approvalsLink = navigation.getByRole("link", { name: "Approvals 1", exact: true });
  await approvalsLink.click();
  await expect(page).toHaveURL(/#approvals$/);
  await expect(approvalsLink).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("heading", { name: "Approval inbox", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Applying decision…", exact: true })).toBeDisabled();
  expect(decisions).toBe(1);
  gate.release();
  await expect(page.getByText(/A new action needs a separate approval/)).toBeVisible();
  await expect(page.getByLabel("Exact arguments for write_file")).toContainText("second.txt");
  await expect(page.getByRole("checkbox")).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Approve & run", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Deny action", exact: true }).click();
  await expect(page.getByText("Nothing waiting on you.", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "My workspace", exact: true }).click();
  await expect(page.getByText("Finished after respecting your denied second action.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Review approval", exact: false })).toHaveCount(0);
  expect(decisions).toBe(2);
});

test("late saved result never reopens a cleared conversation", async ({ page }) => {
  const gate = latch(); let readRequested = false;
  await setup(page, async (route, path) => {
    if (path === "/runs/stream") {
      await route.fulfill({ headers: { ...headers, "content-type": "text/event-stream" }, body: `event: run.started\ndata: {"data":{"run_id":"saved-run"}}\n\nevent: run.completed\ndata: {"data":{"answer":"Initial reply"}}\n\nevent: end\ndata: {"status":"completed"}\n\n` }); return true;
    }
    if (path === "/runs/saved-run") { readRequested = true; await gate.promise; await route.fulfill({ headers, json: completed(makeRun(), "Late saved answer") }).catch(() => {}); return true; }
    return false;
  });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Continue safely");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Initial reply", { exact: true })).toBeVisible();
  await expect.poll(() => readRequested).toBe(true);
  await page.getByRole("button", { name: "New chat", exact: true }).click(); gate.release();
  await expect(page.getByText("Late saved answer", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Big ideas start with a conversation." })).toBeVisible();
});

test("desktop access is unpaired and unavailable even while API is connected", async ({ page }) => {
  await setup(page, async () => false); await page.goto("/#desktop");
  await expect(page.getByText("API connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Disconnected · not paired", exact: true })).toBeVisible();
  await expect(page.getByText("Built · unavailable", { exact: true })).toHaveCount(3);
  await expect(page.getByText("Not implemented", { exact: true })).toHaveCount(3);
  await expect(page.getByRole("button", { name: /^(Connect|Pair|Grant)/ })).toHaveCount(0);
  await expect(page.getByText(/Connections granted to dot or another assistant do not grant JARVIS access/)).toBeVisible();
  await page.screenshot({ path: "test-results/desktop-bridge-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "Open navigation", exact: true })).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("button", { name: "Close navigation", exact: true })).toHaveCount(0);
  const sidebar = page.locator(".sidebar");
  await expect(sidebar).toHaveAttribute("aria-hidden", "true");
  // Resizing starts a slide-out transition. Verify its real final position,
  // rather than recording a half-open menu or hiding a navigation regression.
  await expect.poll(() => sidebar.evaluate((element) => Math.ceil(element.getBoundingClientRect().right))).toBeLessThanOrEqual(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  // Narrow cards keep capability IDs intact, with their availability underneath.
  expect(await page.locator(".desktop-capability").evaluateAll((cards) => cards.every((card) => {
    const name = card.querySelector("small")!;
    const badge = card.querySelector(".run-status")!;
    return name.getClientRects().length === 1 && badge.getBoundingClientRect().top >= name.getBoundingClientRect().bottom;
  }))).toBe(true);
  await page.screenshot({ path: "test-results/desktop-bridge-mobile.png", fullPage: true, animations: "disabled" });
});

test("desktop endpoint failure does not pretend disconnected status was checked and recovers", async ({ page }) => {
  let fail = true;
  await setup(page, async (route, path) => {
    if (path !== "/desktop/status" || !fail) return false;
    await route.fulfill({ headers, status: 503, json: { detail: "Bridge status temporarily unavailable" } }); return true;
  });
  await page.goto("/#desktop");
  await expect(page.getByRole("heading", { name: "Status unavailable", exact: true })).toBeVisible();
  await expect(page.getByText("API connected", { exact: true })).toBeVisible();
  await expect(page.getByText("Built · unavailable", { exact: true })).toHaveCount(0);
  fail = false; await page.getByRole("button", { name: "Refresh desktop status", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Disconnected · not paired", exact: true })).toBeVisible();
  await expect(page.getByText("Bridge status temporarily unavailable", { exact: true })).toHaveCount(0);
});

test("a delayed status read cannot overwrite a confirmed resume", async ({ page }) => {
  let run = makeRun(); let reads = 0; let oldReadWaiting = false;
  const gate = latch(); const stale = makeRun();
  await setup(page, async (route, path) => {
    if (path === "/runs/saved-run") {
      reads++;
      if (reads === 2) { oldReadWaiting = true; await gate.promise; await route.fulfill({ headers, json: stale }).catch(() => {}); }
      else await route.fulfill({ headers, json: run });
      return true;
    }
    if (path.endsWith("/resume")) { run = completed(run, "Newest confirmed result"); await route.fulfill({ headers, json: run }); return true; }
    return runRead(route, path, run);
  });
  await openRun(page);
  await expect(page.getByRole("button", { name: "Resume run", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Refresh saved status", exact: true }).click();
  await expect.poll(() => oldReadWaiting).toBe(true);
  await page.getByRole("button", { name: "Resume run", exact: true }).click();
  await expect(page.getByText("Newest confirmed result", { exact: true })).toBeVisible();
  gate.release();
  await expect(page.getByText("Newest confirmed result", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Resume run", exact: true })).toHaveCount(0);
});

test("an unconfirmed approval stays locked until its pending state is checked", async ({ page }) => {
  let decisions = 0; let pendingReads = 0;
  const approval = { id: "lost-decision", toolName: "write_file", risk: "write" as const, args: { path: "/workspace/review.txt", content: "Fixture" }, requestedAt: now };
  await setup(page, async (route, path) => {
    if (path === "/tools/lost-decision/approve") { decisions++; await route.abort("failed"); return true; }
    if (path === "/tools/pending") { pendingReads++; await route.fulfill({ headers, json: [{ id: approval.id, status: "pending" }] }); return true; }
    return false;
  }, () => [approval]);
  await page.goto("/#approvals"); await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Approve & run", exact: true }).click();
  await expect(page.getByRole("button", { name: "Check saved decision", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Deny action", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "Check pending approvals", exact: true }).click();
  await expect(page.getByRole("button", { name: "Approve & run", exact: true })).toBeEnabled();
  expect(decisions).toBe(1); expect(pendingReads).toBe(1);
});
