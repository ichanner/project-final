import { client, MODEL, estimateCost } from "./anthropicClient.js";

const SYSTEM_PROMPT = `You are a structured-data extractor. You receive raw HTML from a single web page and a description of the data the user wants. Your job is to find the repeating structured region (table, card grid, list, etc.) that matches the description and return the entities as a JSON array.

Rules:
- Anchor on semantics ("the rate filings table") rather than CSS selectors. The site may redesign without notice.
- Each entity must conform to the provided schema. If a field is missing on a page, use null — do not invent values.
- Return a confidence score in [0, 1] for the extraction as a whole. Use lower confidence when the page does not appear to contain the expected region.
- Skip header rows, navigation chrome, ads, and footers.`;

const HTML_HARD_CAP = 200_000;

function trimHtml(html) {
  if (html.length <= HTML_HARD_CAP) return html;
  return html.slice(0, HTML_HARD_CAP);
}

function buildOutputSchema(userSchema) {
  const props = userSchema?.fields && typeof userSchema.fields === "object"
    ? userSchema.fields
    : { value: { type: "string" } };

  const entityProps = {};
  const required = [];
  for (const [name, def] of Object.entries(props)) {
    entityProps[name] = typeof def === "string" ? { type: def } : def;
    required.push(name);
  }

  return {
    type: "object",
    properties: {
      confidence: { type: "number" },
      entities: {
        type: "array",
        items: {
          type: "object",
          properties: entityProps,
          required,
          additionalProperties: false,
        },
      },
    },
    required: ["confidence", "entities"],
    additionalProperties: false,
  };
}

export async function extract({ html, schema, anchor }) {
  const description = anchor
    ? `Find the region described as: "${anchor}". Extract every entity in that region.`
    : "Find the most prominent repeating data region on the page and extract its entities.";

  const userMessage = [
    description,
    "",
    "Schema for each entity:",
    JSON.stringify(schema?.fields ?? { value: "string" }, null, 2),
    "",
    "HTML follows. Identify the matching region semantically — DOM paths are not stable.",
    "---",
    trimHtml(html),
  ].join("\n");

  const outputSchema = buildOutputSchema(schema);

  const response = await client.chat.completions.create({
    model: MODEL,
    max_tokens: 16000,
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: userMessage },
    ],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "extraction_result",
        strict: true,
        schema: outputSchema,
      },
    },
  });

  const content = response.choices?.[0]?.message?.content ?? "";
  // Some OpenRouter providers (notably Claude via Bedrock) ignore the
  // response_format: json_schema and embed the JSON in a ```json ... ``` block
  // surrounded by chain-of-thought prose. Extract the fenced block, otherwise
  // fall back to the largest JSON-looking substring.
  let payload = content.trim();
  const fenceMatch = payload.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  if (fenceMatch) {
    payload = fenceMatch[1].trim();
  } else {
    const first = payload.indexOf("{");
    const last = payload.lastIndexOf("}");
    if (first !== -1 && last > first) payload = payload.slice(first, last + 1);
  }
  let parsed;
  try {
    parsed = JSON.parse(payload);
  } catch {
    parsed = { confidence: 0, entities: [] };
  }

  return {
    backend: "cloud",
    model: MODEL,
    entities: parsed.entities ?? [],
    confidence: typeof parsed.confidence === "number" ? parsed.confidence : 0.8,
    cost_usd: estimateCost(response.usage),
    usage: response.usage,
  };
}
