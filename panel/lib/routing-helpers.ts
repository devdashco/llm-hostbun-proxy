// Routing rule encoding, ported from admin/ui/pages/routing.js. A rule is {provider,model} (a pin),
// {block:true} (reject), or null (auto). The <Select> encodes it as `provider|model`, '' = auto,
// '__block__' = block.
export const BLOCK_VAL = "__block__";

export interface Rule {
  provider?: string;
  model?: string;
  block?: boolean;
  allowProviders?: string[];
  allowModels?: string[];
}

export const PROJ_MODELS: { provider: string; model: string }[] = [
  { provider: "claudecode", model: "claude-sonnet-4-6" },
  { provider: "claudecode", model: "claude-opus-4-8" },
  { provider: "claudecode", model: "claude-haiku-4-5-20251001" },
  { provider: "crazyrouter", model: "gemini-2.5-flash-lite" },
  { provider: "crazyrouter", model: "gemini-2.5-flash" },
  { provider: "crazyrouter", model: "gemini-2.5-pro" },
  { provider: "local", model: "gemma-4-e4b-it-obliterated" },
  { provider: "local", model: "google/gemma-4-26b-a4b" },
];

export const valToRule = (v: string): Rule | null => {
  if (!v) return null;
  if (v === BLOCK_VAL) return { block: true };
  const [provider, ...rest] = v.split("|");
  return { provider, model: rest.join("|") };
};

export const ruleToVal = (cur: Rule | null): string =>
  cur && !cur.block ? `${cur.provider}|${cur.model}` : cur && cur.block ? BLOCK_VAL : "";

export const LIM_WINDOWS = ["1h", "6h", "24h", "7d", "30d"];
export const LIM_HARD: [string, string][] = [
  ["block", "block (429)"],
  ["slow", "slow only"],
  ["warn", "warn only"],
];
