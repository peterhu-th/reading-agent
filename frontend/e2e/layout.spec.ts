import { expect, test } from "@playwright/test";

const session = {
  session_id: "session",
  title: "新对话",
  turns: [],
  active_book_titles: [],
  active_authors: [],
  active_entities: [],
  active_topics: [],
  explicit_book_titles: [],
  rolling_summary: "",
  updated_at: "2026-01-01",
};
const readerParagraphs = Array.from({ length: 70 }, (_, index) => ({
  paragraph_index: index,
  edit_id: `paragraph-${index}`,
  text: index === 0 ? "哈里在城市中独自生活，他不断审视自己与世界的距离。" : `这是用于滚动验收的第 ${index + 1} 段正文，内容应当只在中央阅读区域内滚动。`,
  kind: "paragraph",
  annotations: [],
}));

test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/health") {
      await route.fulfill({ json: { status: "ok", answer_api_healthy: false, answer_provider_ready: false, chunk_index_exists: true, chunks_exist: true, embedding_model_exists: true, reranker_model_exists: true } });
      return;
    }
    if (url.pathname === "/api/editor/state") {
      await route.fulfill({ json: { dirty: false, undo_count: 0, last_operation: "", database_updating: false, database_status: "idle", database_message: "" } });
      return;
    }
    if (url.pathname === "/api/reader/books") {
      await route.fulfill({ json: [{ book_id: "book", title: "荒原狼", author: "黑塞", book_type: "fiction", chapter_count: 2, paragraph_count: 16 }] });
      return;
    }
    if (url.pathname === "/api/reader/books/book/chapters") {
      await route.fulfill({ json: [
        { book_id: "book", chapter_index: 0, chapter_title: "第一章", depth: 0, paragraph_count: 8, preview: "哈里在城市中独自生活。" },
        { book_id: "book", chapter_index: 1, chapter_title: "第二章", depth: 0, paragraph_count: 8, preview: "他开始重新观察自己的生活。" },
      ] });
      return;
    }
    if (url.pathname === "/api/reader/books/book/chapters/0") {
      await route.fulfill({ json: {
        book: { book_id: "book", title: "荒原狼", author: "黑塞", book_type: "fiction", chapter_count: 2, paragraph_count: 16 },
        chapter: { book_id: "book", chapter_index: 0, chapter_title: "第一章", depth: 0, paragraph_count: 8, preview: "" },
        paragraphs: readerParagraphs,
        offset: 0,
        next_offset: null,
        previous_chapter_index: null,
        next_chapter_index: 1,
      } });
      return;
    }
    if (url.pathname === "/api/sessions") {
      await route.fulfill(route.request().method() === "POST" ? { status: 201, json: session } : { json: [] });
      return;
    }
    await route.fulfill({ status: 404, json: { detail: "mock route missing" } });
  });
});

test("reader is primary and chat can be hidden", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await expect(page.getByText("哈里在城市中独自生活", { exact: false })).toBeVisible();
  await expect(page.locator(".library-sidebar")).toHaveClass(/is-open/);
  await page.locator(".reader-scroll").hover();
  await page.mouse.wheel(0, 700);
  await expect.poll(() => page.locator(".reader-scroll").evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: "询问 AI" })).toBeVisible();
  await page.getByRole("button", { name: "询问 AI" }).click();
  await expect(page.getByText("阅读助理")).toBeVisible();
  await expect(page.getByPlaceholder("直接询问这本书…")).toBeEditable();
  await page.getByTitle("隐藏聊天").click();
  await expect(page.locator(".chat-drawer")).not.toHaveClass(/is-open/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/desktop-reader.png", fullPage: true });
});

test("mobile panels remain usable without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByText("哈里在城市中独自生活", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "询问 AI" }).click();
  await expect(page.getByText("阅读助理")).toBeVisible();
  await page.waitForTimeout(250);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/mobile-reader.png", fullPage: true });
});

test("refresh with a remembered chat width does not leave a blank column", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem("reader-library-open", "false");
    localStorage.setItem("reader-chat-open", "false");
    localStorage.setItem("reader-chat-width", "560");
  });
  await page.goto("/");
  await expect(page.getByText("哈里在城市中独自生活", { exact: false })).toBeVisible();

  const layout = await page.evaluate(() => ({
    viewport: window.innerWidth,
    appWidth: document.querySelector(".reader-app")?.getBoundingClientRect().width,
    mainRight: document.querySelector(".reader-main")?.getBoundingClientRect().right,
    hiddenChatWidth: document.querySelector(".chat-drawer")?.getBoundingClientRect().width,
  }));

  expect(layout.appWidth).toBe(layout.viewport);
  expect(layout.mainRight).toBeCloseTo(layout.viewport ?? 0, 0);
  expect(layout.hiddenChatWidth).toBeLessThanOrEqual(1);
});
