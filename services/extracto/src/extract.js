import { client, DEFAULT_MODEL, estimateCost } from "./anthropicClient.js";

// Hybrid mode: a single LLM call returns a CSS-selector recipe AND the
// FIRST FEW entities as a verification probe. The recipe is the cost-saver
// (cached, BS4-applied on subsequent polls). The 3-entity sample is a
// fallback for when BS4 verification fails — limited but sufficient to keep
// the system useful while we wait for a re-anchor.
//
// We deliberately do NOT request the full entity list — page can have 1000+
// rows and the response would blow past max_tokens. BS4 handles bulk extract.
const HYBRID_SYSTEM_PROMPT = `You receive raw HTML from a single web page and a description of a homogeneous collection of entities. You return a JSON object with:

(1) A CSS-selector RECIPE that a downstream BeautifulSoup engine will apply to extract every entity from this page (and every future poll of the same page). When the recipe works, no future LLM calls are needed.

(2) The first 3 entities as a sample, used to verify the recipe and as a small fallback if BS4 can't apply it.

Recipe rules:
- root_selector: a CSS selector matching EVERY row in the repeating collection (one match = one entity). Test it mentally: how many rows would this match? It must equal expected_count.
- fields: for each schema field, a sub-selector relative to root + an extract method.
  - extract: "text", "attr:<name>" (e.g. "attr:href"), or "html".
  - transform: "parseFloat" (numbers), "parseInt", "trim", "lower", "upper", or null.
- IDENTITY field "{{IDENTITY_FIELD}}" must use the most stable selector available — prefer plain visible text on stable elements; avoid auto-generated IDs, hash-suffixed classes, timestamps.
- expected_count: integer estimate of total rows root_selector matches on this page.
- Use the schema's field names verbatim. Don't invent fields.

Entities rules:
- Return UP TO THE FIRST 30 entities. Stop after 30 even if more exist — BS4 will get the rest from the recipe.
- Field types must match the schema (numbers as numbers, not strings).
- Use null for missing fields.

Reply with ONLY a JSON object. No prose, no fences. Top-level keys: root_selector, expected_count, fields, entities, confidence.`;

const HTML_HARD_CAP = Number(process.env.EXTRACTO_HTML_CAP ?? 300_000);

// Strip noise (head, scripts, styles, comments, svgs) before applying byte cap.
function preprocessHtml(html) {
  return html
    .replace(/<!--[\s\S]*?-->/g, "")
    .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, "")
    .replace(/<style\b[^<]*(?:(?!<\/style>)<[^<]*)*<\/style>/gi, "")
    .replace(/<noscript\b[^<]*(?:(?!<\/noscript>)<[^<]*)*<\/noscript>/gi, "")
    .replace(/<svg\b[^<]*(?:(?!<\/svg>)<[^<]*)*<\/svg>/gi, "")
    .replace(/<head\b[^<]*(?:(?!<\/head>)<[^<]*)*<\/head>/gi, "<head></head>");
}

// Try to focus on the most data-rich content area before applying the cap.
// Most pages put nav/header/footer in known places; we prefer <main>,
// <article>, or the largest table/list block when present.
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
  // First try: focus on a content region.
  const focused = focusContent(cleaned);
  if (focused.length <= HTML_HARD_CAP) return focused;
  // Otherwise: start at <body> if available, else just slice.
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
    // Anchor recipe ~600 tokens + 30 entity sample @ ~80 tokens each = ~3000.
    // 8000 leaves headroom for chatty models without blowing past 128K context
    // limit on gpt-4o (input HTML is the main consumer there, not output).
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
    // Use the first LLM entity as the BS4 verification probe.
    verification: entities[0] ?? null,
  } : null;

  return {
    model: useModel,
    anchors,
    entities,                     // direct fallback if anchors fail BS4 verification
    confidence: typeof parsed.confidence === "number" ? parsed.confidence : 0.0,
    cost_usd: estimateCost(useModel, response.usage),
    usage: response.usage,
  };
}
