import OpenAI from "openai";

// All cloud calls go through OpenRouter. The filename is historical — it used
// to wrap the Anthropic SDK directly.
export const client = new OpenAI({
  apiKey: process.env.OPENROUTER_API_KEY,
  baseURL: "https://openrouter.ai/api/v1",
  defaultHeaders: {
    "HTTP-Referer": "https://github.com/ichanner/project-final",
    "X-Title": "WebHarvest",
  },
});

// Default model when a request omits one. Per-source model selection is the
// normal path; this is just the fallback.
export const DEFAULT_MODEL =
  process.env.EXTRACTO_DEFAULT_MODEL || "anthropic/claude-sonnet-4";

// Per-1M-token pricing (USD). These match OpenRouter's published rates as of
// April 2026 for the four models we route across. If you add a model, add it
// here too — otherwise cost reports will read 0 for that model.
export const PRICING = {
  "anthropic/claude-sonnet-4":         { input: 3.00,  output: 15.00 },
  "openai/gpt-4o":                     { input: 2.50,  output: 10.00 },
  "meta-llama/llama-3.3-70b-instruct": { input: 0.13,  output: 0.40  },
  "google/gemini-2.0-flash-001":       { input: 0.10,  output: 0.40  },
};

export function estimateCost(model, usage) {
  const rate = PRICING[model];
  if (!rate || !usage) return 0;
  const inTok = usage.prompt_tokens ?? 0;
  const outTok = usage.completion_tokens ?? 0;
  return (inTok * rate.input + outTok * rate.output) / 1_000_000;
}
