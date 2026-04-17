/** Mirrors backend `shopifyseo/dashboard_ai_engine_parts/settings.py` provider key checks. */

export function isProviderKeyReady(provider: string, values: Record<string, string>): boolean {
  const p = (provider || "openai").trim().toLowerCase();
  if (p === "openai") return Boolean((values.openai_api_key || "").trim());
  if (p === "gemini") return Boolean((values.gemini_api_key || "").trim());
  if (p === "anthropic") return Boolean((values.anthropic_api_key || "").trim());
  if (p === "openrouter") return Boolean((values.openrouter_api_key || "").trim());
  if (p === "ollama") return Boolean((values.ollama_base_url || "").trim());
  return false;
}
