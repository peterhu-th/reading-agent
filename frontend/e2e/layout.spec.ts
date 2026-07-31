import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/health", (route) => route.fulfill({ json: { status: "ok", answer_api_healthy: true, answer_provider_ready: true, chunk_index_exists: true, chunks_exist: true, embedding_model_exists: true, reranker_model_exists: true } }));
  await page.route("**/api/books", (route) => route.fulfill({ json: [{ book_id: "book", title: "荒原狼", author: "黑塞", book_type: "fiction", chapter_count: 12, chunk_count: 120 }] }));
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 201, json: { session_id: "session", title: "新对话", turns: [], active_book_titles: [], active_authors: [], active_entities: [], active_topics: [], explicit_book_titles: [], rolling_summary: "", updated_at: "2026-01-01" } });
    } else {
      await route.fulfill({ json: [] });
    }
  });
});

test("renders chat and library without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByText("Reading Memory")).toBeVisible();
  await expect(page.getByText("荒原狼", { exact: true })).toBeVisible();
  await expect(page.getByPlaceholder("询问情节、人物、主题，或比较多本书…")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/desktop-layout.png", fullPage: true });
});

test("uses drawers on a mobile viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByTitle("打开侧栏").click();
  await expect(page.getByText("荒原狼", { exact: true })).toBeVisible();
  await page.waitForTimeout(250);
  const drawer = await page.locator(".left-panel").boundingBox();
  expect(drawer?.width ?? 0).toBeGreaterThan(300);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/mobile-layout.png", fullPage: true });
});
