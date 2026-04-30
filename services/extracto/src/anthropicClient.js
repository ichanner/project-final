import OpenAI from "openai";

// Despite the filename, this client points at OpenRouter, which is OpenAI-API
// compatible. The filename is kept so the rest of the import graph doesn't churn.
export const client = new OpenAI({
  apiKey: process.env.OPENROUTER_API_KEY,
  baseURL: "https://openrouter.ai/api/v1",
  defaultHeaders: {
    "HTTP-Referer": "https://github.com/ichanner/project",
    "X-Title": "WebHarvest",
  },
});

export const MODEL = process.env.EXTRACTO_MODEL || "anthropic/claude-sonnet-4";

// Per-1M-token pricing (USD). Defaults match OpenRouter's published rate for
// anthropic/claude-sonnet-4. Override via env if EXTRACTO_MODEL is changed.
export const PRICING = {
  inputPerMillion: Number(process.env.EXTRACTO_INPUT_PRICE ?? 3.0),
  outputPerMillion: Number(process.env.EXTRACTO_OUTPUT_PRICE ?? 15.0),
};

export function estimateCost(usage) {
  if (!usage) return 0;
  const input = (usage.prompt_tokens ?? 0) * PRICING.inputPerMillion;
  const output = (usage.completion_tokens ?? 0) * PRICING.outputPerMillion;
  return (input + output) / 1_000_000;
}
