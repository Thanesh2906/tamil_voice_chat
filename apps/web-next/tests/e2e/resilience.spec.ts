import { test, expect, type Page, type Route } from "@playwright/test";

const now = "2026-10-07T19:00:00Z";
const agent = {
  id: "manager",
  name: "JARVIS",
  role: "Manager",
  description: "Plans and coordinates work.",
  mode: "personal",
  capabilities: ["conversation"],
  tools_enabled: true,
  available: true,
};
const snapshot = {
  generatedAt: now,
  agents: [{ ...agent, status: "idle" }],
  events: [],
  tasks: [],
  approvals: [],
};
const headers = {
  "access-control-allow-origin": "http://127.0.0.1:3001",
  "access-control-allow-headers": "authorization,content-type",
  "access-control-allow-methods": "GET,POST,OPTIONS",
};

type Handler = (route: Route, path: string) => Promise<boolean>;
async function setup(page: Page, handler: Handler) {
  await page.addInitScript(() => {
    sessionStorage.setItem("jarvis_access_token", "resilience-fixture");
    sessionStorage.setItem(
      "jarvis_refresh_token",
      "resilience-refresh-fixture",
    );
  });
  await page.route("http://127.0.0.1:8000/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() === "OPTIONS") {
      await route.fulfill({ status: 204, headers });
      return;
    }
    if (await handler(route, path)) return;
    const body: Record<string, unknown> = {
      "/me": {
        id: "fixture",
        display_name: "Tester",
        email: "test@example.invalid",
        scopes: ["chat"],
        preferred_language: "en",
        created_at: now,
      },
      "/agents": {
        agents: [agent],
        execution: {
          mode: "inline",
          durable_queue: false,
          approval_resume: false,
        },
      },
      "/models": {
        allow_cloud: false,
        providers: [
          {
            name: "ollama",
            privacy: "local",
            default_model: "fixture",
            configured: true,
          },
        ],
      },
      "/projects": [],
      "/runs": [],
      "/api/v1/office/snapshot": snapshot,
    };
    await route.fulfill({ headers, json: body[path] ?? {} });
  });
}

test("office reports a disconnected API and clears the stale error after recovery", async ({
  page,
}) => {
  let fail = true;
  await setup(page, async (route, path) => {
    if (path !== "/api/v1/office/snapshot" || !fail) return false;
    await route.fulfill({
      status: 503,
      headers,
      json: { detail: "Fixture temporarily unavailable" },
    });
    return true;
  });
  await page.goto("/#approvals");
  await expect(
    page.getByText("API disconnected", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("status")
      .filter({ hasText: "Fixture temporarily unavailable" }),
  ).toBeVisible();
  fail = false;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByText("API connected", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Fixture temporarily unavailable", { exact: false }),
  ).toHaveCount(0);
  await expect(page.getByText("Nothing waiting on you.")).toBeVisible();
});

test("late run detail cannot replace a newer selection or reopen dismissed detail", async ({
  page,
}) => {
  let releaseA: (() => void) | undefined;
  let requestedA = false;
  const blockedA = new Promise<void>((resolve) => {
    releaseA = resolve;
  });
  const runs = ["a", "b"].map((id) => ({
    id,
    agent_id: "manager",
    status: "completed",
    input_preview: `Run ${id.toUpperCase()}`,
    created_at: now,
  }));
  await setup(page, async (route, path) => {
    if (path === "/runs") {
      await route.fulfill({ headers, json: runs });
      return true;
    }
    if (path === "/runs/a" || path === "/runs/b") {
      const id = path.endsWith("a") ? "a" : "b";
      if (id === "a") {
        requestedA = true;
        await blockedA;
      }
      await route
        .fulfill({
          headers,
          json: {
            id,
            status: "completed",
            input_text: `Run ${id}`,
            created_at: now,
            events: [
              {
                sequence: 1,
                type: "run.completed",
                data: { answer: `Saved response ${id.toUpperCase()}` },
                created_at: now,
              },
            ],
          },
        })
        .catch(() => {});
      return true;
    }
    return false;
  });
  await page.goto("/#runs");
  const first = page.getByRole("button", { name: /^Run A/ });
  const second = page.getByRole("button", { name: /^Run B/ });
  await first.click();
  await expect.poll(() => requestedA).toBe(true);
  await second.click();
  await expect(
    page.getByText("Saved response B", { exact: true }),
  ).toBeVisible();
  releaseA?.();
  await expect(second).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByText("Saved response A", { exact: true })).toHaveCount(
    0,
  );
  await second.click();
  await expect(page.locator(".run-detail")).toHaveCount(0);
  await expect(first).toHaveAttribute("aria-expanded", "false");
});

test("chat reuses a session for follow-ups and rotates it for New chat", async ({
  page,
}) => {
  const sessions: string[] = [];
  await setup(page, async (route, path) => {
    if (path !== "/runs/stream") return false;
    const request = route.request().postDataJSON();
    sessions.push(request.session_id);
    await route.fulfill({
      headers: { ...headers, "content-type": "text/event-stream" },
      body: `event: run.completed\ndata: ${JSON.stringify({ sequence: 1, data: { answer: `Received ${request.message}` } })}\n\nevent: end\ndata: {"status":"completed"}\n\n`,
    });
    return true;
  });
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "Message", exact: true });
  await input.fill("first");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Received first", { exact: true })).toBeVisible();
  await input.fill("follow-up");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(
    page.getByText("Received follow-up", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "New chat", exact: true }).click();
  await input.fill("fresh");
  await page.getByRole("button", { name: "Send message", exact: true }).click();
  await expect(page.getByText("Received fresh", { exact: true })).toBeVisible();
  expect(sessions).toHaveLength(3);
  expect(sessions[0]).toBeTruthy();
  expect(sessions[1]).toBe(sessions[0]);
  expect(sessions[2]).not.toBe(sessions[0]);
});
