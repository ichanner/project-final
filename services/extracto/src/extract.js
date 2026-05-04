import { client, DEFAULT_MODEL, estimateCost } from "./anthropicClient.js";

const HYBRID_SYSTEM_PROMPT = `You receive raw HTML from a single web page and a description of a homogeneous collection of entities. You return a JSON object with:

(1) A CSS-selector RECIPE that a downstream BeautifulSoup engine will apply on every future poll. Subsequent polls will NOT call you — they apply this recipe deterministically. So getting it right matters.

(2) The first 30 entities as a sample (verification + bootstrap).

Field roles (CRUCIAL — read this carefully):
- ANCHOR fields are stable identifiers. They are how we LOCATE this entity in future scrapes and how we tell entities apart. Their selector must produce byte-stable text — prefer visible content of stable elements; avoid whitespace-noisy nodes; do NOT include footnote markers or wrapper spans that vary.
- VOLATILE fields are the values we are watching for change over time. Their selector must target the cell that actually contains the changing value (price, score, count, status). Cells next to anchor fields in the same row are usually the right targets.

Recipe rules:
- root_selector: ONE CSS selector matching EVERY row in the target collection — and ONLY that collection. If the page has multiple tables, scope it tightly (e.g. \`table#primary tbody tr\`, NOT \`table.wikitable tbody tr\`). expected_count must be an honest estimate.
- fields: for each schema field, a sub-selector relative to root + extract + transform.
  - extract: "text", "attr:<name>", or "html".
  - transform: "parseFloat", "parseInt", "trim", "lower", "upper", or null.
- IDENTITY (primary anchor) field "{{IDENTITY_FIELD}}" — selector must be byte-stable across re-fetches.
- Use the schema's field names verbatim.

Entities rules:
- Up to first 30 entities. Field types must match schema. Use null for missing.

Reply with ONLY a JSON object. No prose, no fences. Top-level keys: root_selector, expected_count, fields, entities, confidence.

The schema you receive includes a "role" annotation per field. Honor it. Volatile-field selectors should target nodes whose textContent is JUST that value (no surrounding noise).`;

const HTML_HARD_CAP = Number(process.env.EXTRACTO_HTML_CAP ?? 300_000);

function preprocessHtml(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "")
    .replace(/<noscript\b[^<]*(?:(?!<\/noscript>)<[^<]*)*<\/noscript>/gi, "")
    .replace(/<svg\b[^<]*(?:(?!<\/svg>)<[^<]*)*<\/svg>/gi, "")
    .replace(/<head\b[^<]*(?:(?!<\/head>)<[^<]*)*<\/head>/gi, "<head></head>");
}

function focusContent(html) {
  const mainMatch = html.match(/<main\b[\s\S]*?<\/main>/i);
  if (mainMatch && mainMatch[0].length > 1000) return mainMatch[0];
  const articleMatch = html.match(/<article\b[\s\S]*?<\/article>/i);
  if (articleMatch && articleMatch[0].length > 1000) return articleMatch[0];
  return html;
}

function trimHtml(html) {
  const cleaned = preprocessHtml(html);
  if (cleaned.length <= HTML_HARD_CAP) return cleaned;
  const focused = focusContent(cleaned);
  if (focused.length <= HTML_HARD_CAP) return focused;
  const bodyMatch = focused.match(/<body\b[^>]*>/i);
  const start = bodyMatch ? bodyMatch.index : 0;
  return focused.slice(start, start + HTML_HARD_CAP);
}

function buildHybridOutputSchema(userSchema) {
  const userFields = userSchema?.fields && typeof userSchema.fields === "object"
    ? userSchema.fields
    : { value: { type: "string" } };
  const fieldNames = Object.keys(userFields);

  const fieldRecipeProps = {};
  for (const name of fieldNames) {
    fieldRecipeProps[name] = {
      type: "object",
      properties: {
        selector: { type: "string" },
        extract: { type: "string" },
        transform: { type: ["string", "null"] },
      },
      required: ["selector", "extract", "transform"],
      additionalProperties: false,
    };
  }

  const entityProps = {};
  const required = [];
  for (const [name, def] of Object.entries(userFields)) {
    entityProps[name] = typeof def === "string" ? { type: def } : def;
    required.push(name);
  }

  return {
    type: "object",
    properties: {
      root_selector: { type: "string" },
      expected_count: { type: "integer" },
      fields: {
        type: "object",
        properties: fieldRecipeProps,
        required: fieldNames,
        additionalProperties: false,
      },
      entities: {
        type: "array",
        items: {
          type: "object",
          properties: entityProps,
          required,
          additionalProperties: false,
        },
      },
      confidence: { type: "number" },
    },
    required: ["root_selector", "expected_count", "fields", "entities", "confidence"],
    additionalProperties: false,
  };
}

function parsePayload(content) {
  let payload = (content || "").trim();
  const fenceMatch = payload.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenceMatch) payload = fenceMatch[1].trim();
  else {
    const first = payload.indexOf("{");
    const last = payload.lastIndexOf("}");
    if (first !== -1 && last > first) payload = payload.slice(first, last + 1);
  }
  try { return JSON.parse(payload); }
  catch { return null; }
}

export async function extract({ html, schema, anchor, model, identity_field }) {
  const useModel = model || DEFAULT_MODEL;

  const sys = HYBRID_SYSTEM_PROMPT.replace("{{IDENTITY_FIELD}}", identity_field || "(none)");

  const description = anchor
    ? `Find the region described as: "${anchor}".`
    : "Find the most prominent repeating data region on the page.";

  const userMessage = [
    description,
    "",
    "Schema:",
    JSON.stringify(schema?.fields ?? { value: "string" }, null, 2),
    "",
    "Return: anchor recipe (full) + first 30 entities (capped — BS4 extracts the rest from the recipe if it works).",
    "",
    "HTML follows.",
    "---",
    trimHtml(html),
  ].join("\n");

  const outputSchema = buildHybridOutputSchema(schema);

  const response = await client.chat.completions.create({
    model: useModel,
    max_tokens: Number(process.env.EXTRACTO_MAX_TOKENS ?? 8000),
    messages: [
      { role: "system", content: sys },
      { role: "user", content: userMessage },
    ],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "hybrid_extraction",
        strict: true,
        schema: outputSchema,
      },
    },
  });

  const rawContent = response.choices?.[0]?.message?.content;
  const finishReason = response.choices?.[0]?.finish_reason;
  const parsed = parsePayload(rawContent) || {};
  const entities = Array.isArray(parsed.entities) ? parsed.entities : [];
  if (!parsed.root_selector) {
    console.error(
      `[parse-issue] model=${useModel} finish=${finishReason} ` +
      `parsed_keys=${Object.keys(parsed).join(",")} ` +
      `raw_head=${JSON.stringify((rawContent || "").slice(0, 400))}`
    );
  }

  const anchors = parsed.root_selector ? {
    root_selector: parsed.root_selector,
    expected_count: parsed.expected_count ?? entities.length,
    fields: parsed.fields ?? {},
    verification: entities[0] ?? null,
  } : null;

  return {
    model: useModel,
    anchors,
    entities,
    confidence: typeof parsed.confidence === "number" ? parsed.confidence : 0.0,
    cost_usd: estimateCost(useModel, response.usage),
    usage: response.usage,
  };
}
