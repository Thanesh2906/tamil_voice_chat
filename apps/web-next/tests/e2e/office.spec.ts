import { test, expect, type Page } from "@playwright/test";

const personas = [
  { id: "manager", name: "JARVIS", role: "Manager" },
  { id: "coder", name: "Code Engineer", role: "Engineering" },
  { id: "researcher", name: "Research Analyst", role: "Research" },
  { id: "operator", name: "Operations Analyst", role: "Operations" },
  { id: "writer", name: "Writing Partner", role: "Writing" },
].map((agent) => ({ ...agent, description: "A configured specialist persona.", mode: "personal", capabilities: ["conversation"], tools_enabled: false }));
const models = { allow_cloud: false, providers: [
  { name: "ollama", privacy: "local", default_model: "local-model", configured: true, verified: false, verification_status: "not_checked" },
  ...["openai", "anthropic", "gemini", "xai", "groq", "self_hosted"].map((name) => ({ name, privacy: "cloud", default_model: "configured-by-admin", configured: false, verified: false, verification_status: "not_configured" })),
] };
const now = "2026-10-07T19:00:00Z";

async function fixture(page: Page, options: { signedIn?: boolean; approvals?: boolean } = {}) {
  let approved = false;
  await page.addInitScript(({ signedIn }) => {
    if (signedIn) {
      sessionStorage.setItem("jarvis_access_token", "e2e-session-fixture");
      sessionStorage.setItem("jarvis_refresh_token", "e2e-refresh-fixture");
    }
  }, { signedIn: options.signedIn ?? true });
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const headers = { "access-control-allow-origin": "http://127.0.0.1:3001", "access-control-allow-headers": "authorization,content-type", "access-control-allow-methods": "GET,POST,OPTIONS" };
    if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers });
    let body: unknown = {};
    if (path === "/me") body = { id: "e2e-user", email: "qa@example.invalid", display_name: "QA Tester", preferred_language: "ta", scopes: ["chat", "rag"], created_at: now };
    else if (path === "/models") body = models;
    else if (path === "/agents") body = { agents: personas, execution: { mode: "inline", durable_queue: false, approval_resume: false } };
    else if (path === "/projects") body = [];
    else if (path === "/runs") body = [];
    else if (path === "/api/v1/office/snapshot") body = { generatedAt: now, agents: personas.map((agent) => ({ ...agent, status: "idle" })), events: [], tasks: [], approvals: options.approvals && !approved ? [{ id: "approval-fixture", toolName: "write_file", risk: "write", args: { path: "/workspace/readme.txt", content: "Full proposed content.\nSecond line must be visible." }, requestedAt: now }] : [] };
    else if (path === "/tools/approval-fixture/approve") { approved = true; body = { id: "approval-fixture", status: "completed", result: { bytes_written: 61 } }; }
    else if (path === "/auth/login") body = { access_token: "e2e-session-fixture", refresh_token: "e2e-refresh-fixture", expires_in: 900 };
    else if (path === "/runs/stream") {
      const request = route.request().postDataJSON();
      return route.fulfill({ headers: { ...headers, "content-type": "text/event-stream" }, body: `event: run.started\ndata: ${JSON.stringify({ sequence: 1, data: { agent_id: request.agent_id } })}\n\nevent: token\ndata: ${JSON.stringify({ data: { text: `Fixture answer from ${request.agent_id}.` } })}\n\nevent: run.completed\ndata: ${JSON.stringify({ sequence: 2, data: { answer: `Fixture answer from ${request.agent_id}.` } })}\n\nevent: end\ndata: {"status":"completed"}\n\n` });
    } else return route.fulfill({ status: 404, headers, json: { detail: "Fixture route unavailable" } });
    return route.fulfill({ headers, json: body });
  });
}

test("returning session hydrates without mismatch and navigation preserves separate chats", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page); await page.goto("/");
  await expect(page.getByText("API connected", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Hello manager");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Fixture answer from manager.", { exact: true })).toBeVisible();
  await page.getByLabel("Chat agent").selectOption("coder");
  await expect(page.getByText("Fixture answer from manager.", { exact: true })).toHaveCount(0);
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Review this code");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Fixture answer from coder.", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "AI team", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Meet your AI team" })).toBeVisible();
  await page.goBack();
  await expect(page.getByText("Fixture answer from coder.", { exact: true })).toBeVisible();
  await page.getByLabel("Chat agent").selectOption("manager");
  await expect(page.getByText("Fixture answer from manager.", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  await page.screenshot({ path: "test-results/office-desktop.png", fullPage: true });
});

test("approval shows exact content and requires review before sending one decision", async ({ page }) => {
  await fixture(page, { approvals: true }); await page.goto("/#approvals");
  await expect(page.getByLabel("Exact arguments for write_file")).toContainText("Second line must be visible.");
  const approve = page.getByRole("button", { name: "Approve & run" });
  await expect(approve).toBeDisabled();
  await page.getByRole("checkbox").check(); await expect(approve).toBeEnabled(); await approve.click();
  await expect(page.getByText("Nothing waiting on you.")).toBeVisible();
});

test("provider setup is truthful and keeps credentials out of browser forms", async ({ page }) => {
  await fixture(page); await page.goto("/#providers");
  await expect(page.getByRole("heading", { name: "Your model connections" })).toBeVisible();
  const openai = page.locator(".provider-card").filter({ has: page.getByRole("heading", { name: "OpenAI", exact: true }) });
  await expect(openai).toContainText("Needs setup");
  await openai.getByRole("button", { name: "How to connect" }).click();
  await expect(openai).toContainText("OPENAI_API_KEY");
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await page.screenshot({ path: "test-results/providers-desktop.png", fullPage: true });
});

test("mobile menu closes on navigation and escape without page overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fixture(page); await page.goto("/");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await page.getByRole("link", { name: "AI team", exact: true }).click();
  await expect(page.getByRole("button", { name: "Close navigation" })).toHaveCount(0);
  await page.getByRole("button", { name: "Open navigation" }).click(); await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Close navigation" })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/team-mobile.png", fullPage: true });
});

test("sign-in error recovery and sign-out render consistent screens", async ({ page }) => {
  await fixture(page, { signedIn: false }); await page.goto("/");
  await page.getByLabel("Email or username").fill("qa@example.invalid"); await page.getByLabel("Password", { exact: true }).fill("e2e-only-fixture-password");
  await page.getByRole("button", { name: "Enter workspace" }).click();
  await expect(page.getByLabel("Chat agent")).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Welcome back." })).toBeVisible();
  await page.screenshot({ path: "test-results/signin-desktop.png", fullPage: true });
});

test("voice waits for authentication and recovers from microphone denial", async ({ page }) => {
  await fixture(page);
  await page.addInitScript(() => {
    Object.defineProperty(navigator.mediaDevices, "getUserMedia", { value: async () => { throw new DOMException("Microphone permission denied", "NotAllowedError"); } });
  });
  await page.routeWebSocket("ws://127.0.0.1:8000/voice/session", (socket) => {
    socket.onMessage((raw) => { if (JSON.parse(String(raw)).type === "auth") socket.send(JSON.stringify({ type: "authenticated", data: {} })); });
  });
  await page.goto("/"); await page.getByRole("button", { name: "Start voice", exact: true }).click();
  await expect(page.getByText("Microphone permission denied", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start voice", exact: true })).toBeEnabled();
});

test("voice can cancel while connecting without stale callback or recording", async ({ page }) => {
  const errors: string[] = []; page.on("pageerror", (error) => errors.push(error.message));
  await fixture(page);
  await page.routeWebSocket("ws://127.0.0.1:8000/voice/session", () => { /* intentionally no authentication acknowledgement */ });
  await page.goto("/"); await page.getByRole("button", { name: "Start voice", exact: true }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start voice", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Start voice", exact: true }).click();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  expect(errors).toEqual([]);
});

test("changing workspace cancels the old voice session and starts a clean one", async ({ page }) => {
  await fixture(page);
  await page.route("http://127.0.0.1:8000/projects", (route) => route.fulfill({
    headers: { "access-control-allow-origin": "http://127.0.0.1:3001" },
    json: [{ id: "project-two", name: "Second project", root_path: null }],
  }));
  await page.routeWebSocket("ws://127.0.0.1:8000/voice/session", () => {});
  await page.goto("/");
  await page.getByRole("button", { name: "Start voice", exact: true }).click();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toBeVisible();
  await page.getByLabel("Active project").selectOption("project-two");
  await expect(page.getByRole("button", { name: "Start voice", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Cancel", exact: true })).toHaveCount(0);
});
