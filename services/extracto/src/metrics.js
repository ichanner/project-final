import { collectDefaultMetrics, Counter, Histogram, register } from "prom-client";

collectDefaultMetrics({ prefix: "extracto_" });

export const extractDuration = new Histogram({
  name: "extracto_extract_duration_seconds",
  help: "Cloud extraction duration in seconds",
  labelNames: ["model", "outcome"],
  buckets: [0.5, 1, 2, 5, 10, 20, 60],
});

export const extractTokens = new Counter({
  name: "extracto_tokens_total",
  help: "Tokens consumed during extraction",
  labelNames: ["model", "kind"],
});

export const extractCost = new Counter({
  name: "extracto_cost_usd_total",
  help: "Estimated USD cost",
  labelNames: ["model"],
});

export { register };
