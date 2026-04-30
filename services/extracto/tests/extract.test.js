import { test } from "node:test";
import assert from "node:assert/strict";

import { estimateCost } from "../src/anthropicClient.js";

test("estimateCost handles undefined usage", () => {
  assert.equal(estimateCost(undefined), 0);
  assert.equal(estimateCost(null), 0);
});

test("estimateCost computes from per-million pricing", () => {
  // 1M input + 1M output at sonnet-4-6 pricing -> $3 + $15 = $18.
  const cost = estimateCost({
    input_tokens: 1_000_000,
    output_tokens: 1_000_000,
  });
  assert.equal(cost, 18);
});

test("estimateCost factors in cache reads/writes", () => {
  const cost = estimateCost({
    input_tokens: 0,
    output_tokens: 0,
    cache_read_input_tokens: 1_000_000,
    cache_creation_input_tokens: 1_000_000,
  });
  // 0.3 + 3.75 = 4.05
  assert.equal(cost, 4.05);
});
