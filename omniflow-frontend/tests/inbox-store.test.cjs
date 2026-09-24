const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const { webcrypto } = require("node:crypto");
const vm = require("node:vm");
const ts = require("typescript");

function store(api) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(join(__dirname, "../src/store/inboxStore.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, {
    exports, crypto: webcrypto, console: { error() {} },
    require: (name) => name === "zustand" ? require("zustand") : api,
  });
  return exports.useInboxStore;
}

function message(id, text = "reply") {
  return { id, text, conversationId: "c1", senderType: "human_agent", messageType: "text",
    createdAt: "2026-09-22T00:00:00Z", isRead: true };
}

test("SSE confirmation before REST response leaves exactly one message", async () => {
  let resolve;
  const state = store({ sendMessage: () => new Promise((done) => { resolve = done; }) });
  const sending = state.getState().sendAgentMessage("c1", "reply");
  state.getState().addMessage(message("confirmed"));
  resolve(message("confirmed"));
  await sending;
  assert.equal(state.getState().messages.c1.length, 1);
  assert.equal(state.getState().messages.c1[0].id, "confirmed");
});

test("one failed send preserves a concurrent pending message", async () => {
  const pending = [];
  const state = store({ sendMessage: () => new Promise((resolve, reject) => pending.push({ resolve, reject })) });
  const first = state.getState().sendAgentMessage("c1", "first");
  const failed = assert.rejects(first);
  const second = state.getState().sendAgentMessage("c1", "second");
  pending[0].reject(new Error("Synthetic failure"));
  await failed;
  assert.equal(state.getState().messages.c1.length, 1);
  assert.equal(state.getState().messages.c1[0].text, "second");
  pending[1].resolve(message("second", "second"));
  await second;
});

test("forced refresh recovers missed messages while retaining an in-flight SSE event", async () => {
  let resolve;
  const state = store({ fetchMessages: () => new Promise((done) => { resolve = done; }) });
  state.getState().addMessage(message("cached"));
  const loading = state.getState().loadMessages("c1", true);
  state.getState().addMessage(message("live"));
  // fetchMessages now returns { items, hasMore } (item 12 — cursor pagination
  // + a "load older" hasMore flag), not a bare array.
  resolve({ items: [message("cached"), message("missed")], hasMore: false });
  await loading;
  assert.equal(state.getState().messages.c1.length, 3);
  assert.equal(new Set(state.getState().messages.c1.map((item) => item.id)).size, 3);
});
