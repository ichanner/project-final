import { test } from "node:test";
import assert from "node:assert/strict";

import { estimateCost } from "../src/anthropicClient.js";

test("estimateCost handles undefined usage", () => {
  assert.equal(estimateCost("anthropic/claude-sonnet-4", undefined), 0);
  assert.equal(estimateCost("anthropic/claude-sonnet-4", null), 0);
  assert.equal(estimateCost("missing-model", { prompt_tokens: 1 }), 0);
});

test("estimateCost computes from per-million pricing", () => {
  const cost = estimateCost("anthropic/claude-sonnet-4", {
    prompt_tokens: 1_000_000,
    completion_tokens: 1_000_000,
  });
  assert.equal(cost, 18);
});

test("estimateCost uses the selected model rates", () => {
  const cost = estimateCost("google/gemini-2.0-flash-001", {
    prompt_tokens: 1_000_000,
    completion_tokens: 1_000_000,
  });
  assert.equal(cost, 0.5);
});
