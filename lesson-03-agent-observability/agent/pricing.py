"""Model prices in dollars per million tokens (Claude API list prices, November 2026).
Check https://platform.claude.com/docs/en/about-claude/pricing before relying on them."""

PRICES = {
    #                    input   output  cache read  cache write (5 min)
    "claude-haiku-5-5":  (0.10,  0.50,   0.01,       0.125),
    "claude-sonnet-5-5": (2.00, 10.00,   0.20,       2.50),
    "claude-opus-5-5":   (4.00, 20.00,   0.20,       5.00),
}


def cost_details(model: str, usage: dict) -> dict:
    """usage uses Langfuse's keys: input, output, cache_read_input_tokens, cache_creation_input_tokens.
    Returns the cost of each part and the total, in dollars."""
    p_in, p_out, p_read, p_write = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    parts = {
        "input": usage.get("input", 0) * p_in,
        "output": usage.get("output", 0) * p_out,
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0) * p_read,
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) * p_write,
    }
    parts = {k: round(v / 1_000_000, 8) for k, v in parts.items() if v}
    parts["total"] = round(sum(parts.values()), 8)
    return parts
