/** Regression checks for the DTOs received by both REST and SSE. */
const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

const source = readFileSync(join(__dirname, "../src/lib/api/inbox.ts"), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const exportsObject = {};
vm.runInNewContext(code, {
  exports: exportsObject,
  require: (name) => {
    assert.equal(name, "./client");
    return { apiClient: {} };
  },
});

test("REST and SSE messages keep the conversation ID and media URL", () => {
  const message = exportsObject.mapMessage({
    id: "m1", conversation_id: "c1", sender_type: "human_agent",
    message_type: "audio", text: "hello", s3_media_url: "/audio.mp3",
    created_at: "2026-09-21T00:00:00Z", delivery_status: "QUEUED",
  });
  assert.equal(message.conversationId, "c1");
  assert.equal(message.senderType, "human_agent");
  assert.equal(message.mediaUrl, "/audio.mp3");
  assert.equal(message.deliveryStatus, "QUEUED");
});

test("database conversation statuses match the existing UI filters", () => {
  for (const [input, expected] of [["ai_active", "BOT_ACTIVE"], ["human_active", "AGENT_ACTIVE"], ["ESCALATED", "ESCALATED"]]) {
    assert.equal(exportsObject.mapConversation({ id: "c1", status: input }).status, expected);
  }
});

test("partial SSE updates do not erase customer details", () => {
  const patch = exportsObject.mapConversationPatch({ id: "c1", status: "human_active", is_ai_active: false });
  assert.equal(patch.isAiActive, false);
  assert.equal(patch.customerName, undefined);
  assert.equal(Object.keys(patch).length, 3);
});
