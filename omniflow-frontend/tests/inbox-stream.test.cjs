const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");
const ts = require("typescript");

function setup(getUrl) {
  const timers = new Map();
  const sources = [];
  let timerId = 0;
  const exports = {};
  vm.runInNewContext(ts.transpileModule(readFileSync(join(__dirname, "../src/lib/api/inbox-stream.ts"), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText, {
    exports, setTimeout: (fn) => { timers.set(++timerId, fn); return timerId; },
    clearTimeout: (id) => timers.delete(id),
  });
  const stream = exports.createInboxStream({
    getUrl, subscribe: () => {}, onStatus: () => {}, onOpen: () => {},
    createSource: (url) => {
      const handlers = {};
      const source = { url, closed: false, close() { this.closed = true; },
        addEventListener: (name, callback) => { handlers[name] = callback; },
        emit: (name) => handlers[name]?.(),
      };
      sources.push(source);
      return source;
    },
  });
  return { stream, sources, timers };
}

test("unmount while token is pending never opens a stream", async () => {
  let resolve;
  const state = setup(() => new Promise((done) => { resolve = done; }));
  const pending = state.stream.connect();
  state.stream.disconnect();
  resolve("url-with-token");
  await pending;
  assert.equal(state.sources.length, 0);
});

test("network retry obtains a new token and disconnect cancels retries", async () => {
  let calls = 0;
  const state = setup(async () => `url-token-${++calls}`);
  await state.stream.connect();
  state.sources[0].emit("error");
  assert.equal(state.sources[0].closed, true);
  const retry = [...state.timers.values()][0];
  retry();
  await new Promise(setImmediate);
  assert.equal(state.sources[1].url, "url-token-2");
  state.sources[1].emit("error");
  state.stream.disconnect();
  assert.equal(state.timers.size, 0);
});

test("overlapping connect calls discard the older token response", async () => {
  const resolvers = [];
  const state = setup(() => new Promise((resolve) => resolvers.push(resolve)));
  const first = state.stream.connect();
  const second = state.stream.connect();
  resolvers[1]("new");
  await second;
  resolvers[0]("old");
  await first;
  assert.equal(state.sources.length, 1);
  assert.equal(state.sources[0].url, "new");
});
