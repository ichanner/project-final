import express from "express";

import { extract } from "./extract.js";
import { MODEL } from "./anthropicClient.js";
import { extractCost, extractDuration, extractTokens, register } from "./metrics.js";

const app = express();
app.use(express.json({ limit: "10mb" }));

app.get("/health", (_req, res) => {
  res.json({ status: "ok", model: MODEL });
});

app.get("/metrics", async (_req, res) => {
  res.set("Content-Type", register.contentType);
  res.send(await register.metrics());
});

app.post("/extract", async (req, res) => {
  const { html, schema, anchor } = req.body ?? {};
  if (typeof html !== "string" || html.length === 0) {
    return res.status(400).json({ error: "html (string) required" });
  }

  const startedAt = process.hrtime.bigint();
  let outcome = "ok";
  try {
    const result = await extract({ html, schema, anchor });

    if (result.usage) {
      extractTokens.labels(MODEL, "input").inc(result.usage.prompt_tokens ?? 0);
      extractTokens.labels(MODEL, "output").inc(result.usage.completion_tokens ?? 0);
    }
    extractCost.labels(MODEL).inc(result.cost_usd);

    res.json(result);
  } catch (err) {
    outcome = "error";
    console.error("extract failed:", err);
    res.status(500).json({ error: err.message ?? String(err) });
  } finally {
    const elapsed = Number(process.hrtime.bigint() - startedAt) / 1e9;
    extractDuration.labels(MODEL, outcome).observe(elapsed);
  }
});

const port = Number(process.env.PORT ?? 8081);
app.listen(port, "0.0.0.0", () => {
  console.log(`extracto listening on :${port} model=${MODEL}`);
});
