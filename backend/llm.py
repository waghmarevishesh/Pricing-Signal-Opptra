import json
import os
import re

import truststore
from openai import AsyncOpenAI

from pricing import RECOVER, clamp, describe, required_numbers, rule_recommendation, rule_violations

# Use the OS certificate store so TLS works behind corporate proxies that re-sign traffic.
truststore.inject_into_ssl()

# Any OpenAI-compatible provider works (OpenAI, Cerebras, SambaNova, ...).
API_KEY = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
BASE_URL = os.getenv("LLM_BASE_URL") or None
MODEL = os.getenv("LLM_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Retries honour the provider's Retry-After header on 429s.
_client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, max_retries=4) if API_KEY else None

# Cap output so a runaway reply can't burn credits. Scaled by the number of SKUs in the batch.
TOKENS_BASE = int(os.getenv("LLM_TOKENS_BASE", "400"))
TOKENS_PER_SKU = int(os.getenv("LLM_TOKENS_PER_SKU", "250"))
# gpt-oss models bill hidden reasoning as output tokens, so keep reasoning short.
_extra = {"reasoning_effort": "low"} if "gpt-oss" in MODEL else {}

SYSTEM = """You are a marketplace pricing analyst for Opptra. You give one decision per SKU to a pricing
manager who will act on it with one click.

Hard rules:
- "price" MUST be an integer within [min_price, max_price]. Never below the margin floor.
- "text" is ONE or TWO short sentences with exactly three parts:
  1. WHAT: "Set/Raise/Hold <SKU> to/at Rs.<price>"
  2. WHY: Rs. gap to the competitor and Rs. above the margin floor, and the Buy Box effect.
  3. TRADEOFF: what we give up or risk, in numbers (Rs. per unit change vs today, % cushion over floor,
     buffer left under the competitor, or tie risk).
- % cushion over floor = (price - margin_floor) / price * 100, one decimal. We don't know cost, so call it
  "cushion over floor", not "margin".
- Use the exact numbers, written like Rs.1,189. No vague advice: never write "consider", "mindful",
  "may want", "could".

Good: "Set SKU-001 to Rs.1,189 — Rs.10 below competitor, Rs.139 above margin floor. Recovers Buy Box at
11.7% cushion over floor; gives up Rs.110 per unit versus today."
Bad: "You should consider lowering the price while being mindful of margins."

Reply with JSON only, one entry per input SKU:
{"results": [{"sku": "<sku>", "action": "lower" | "raise" | "hold", "price": <int>, "text": "<sentence>"}]}"""

RECOVER_TASK = """Buy Box is LOST. Pick a price that wins it back: strictly below the competitor when possible,
and never below the margin floor. Prefer a small undercut over a deep cut, because a deeper cut gives away
margin for no extra Buy Box benefit. If max_price equals the competitor price, we can only match at floor:
say the Buy Box becomes a tie and is not guaranteed. action must be "lower"."""

RAISE_TASK = """Buy Box is WON. Decide whether a raise is worth it. A raise is only worth it if it adds at least
2% of our_price per unit; otherwise choose "hold" with price = our_price, because a tiny gain isn't worth
risking the Buy Box. If you raise, leave a buffer of about 1% of the competitor price (at least Rs.5) under
the competitor. A raise never makes the Buy Box safer. Formats:
Raise: "Raise <SKU> to Rs.<price> — Rs.<gap> below competitor, Rs.<x> above margin floor. Keeps Buy Box and
adds Rs.<price - our_price> per unit; buffer under competitor shrinks from Rs.<old gap> to Rs.<new gap>."
Hold: "Hold <SKU> at Rs.<price> — Rs.<gap> below competitor, Rs.<x> above margin floor. Keeps Buy Box; a raise
would add at most Rs.<gap - 1> per unit, too little to risk losing it.\""""

TASKS = f"""Each SKU has a "task" field. Apply the matching instructions.
task "recover": {RECOVER_TASK}
task "raise": {RAISE_TASK}"""

VAGUE = re.compile(r"\b(consider|mindful|may want|might want|perhaps)\b", re.I)


last_reasoning: str | None = None  # the model's working-out from the latest batch call


def enabled() -> bool:
    return _client is not None


VERBS = {"lower": "Set", "raise": "Raise", "hold": "Hold"}


def is_actionable(text: str, s: dict, action: str, price: int) -> bool:
    """True if the sentence leads with the right verb, quotes the price, both gaps and the tradeoff
    correctly, and has no vague advice."""
    quoted = {int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", text)}
    return (text.startswith(VERBS[action]) and required_numbers(s, action, price) <= quoted
            and not VAGUE.search(text))


def _fallback(s: dict, c: dict, error: str | None = None) -> dict:
    rec = {**rule_recommendation(s, c), "source": "rules"}
    if error:
        rec["error"] = error
    return rec


async def recommend_all(pairs: list[tuple[dict, dict]]) -> list[dict]:
    """One LLM call for every SKU (providers often allow only a few requests per minute).
    Each answer is clamped to its allowed range; anything missing or broken falls back to rules."""
    if not pairs:
        return []
    if not _client:
        return [_fallback(s, c) for s, c in pairs]

    rows = []
    for s, c in pairs:
        row = {k: s[k] for k in ("sku", "brand", "our_price", "competitor_price", "margin_floor", "last_changed")}
        row.update(task=c["bucket"], min_price=c["min_price"], max_price=c["max_price"])
        rows.append(row)
    try:
        resp = await _client.chat.completions.create(
            model=MODEL,
            temperature=0.2,
            max_tokens=TOKENS_BASE + TOKENS_PER_SKU * len(rows),
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": f"{TASKS}\n\nSKU data (INR):\n{json.dumps(rows)}"}],
            **_extra,
        )
        global last_reasoning
        last_reasoning = getattr(resp.choices[0].message, "reasoning", None)
        results = json.loads(resp.choices[0].message.content)["results"]
        outs = {o["sku"]: o for o in results if isinstance(o, dict) and "sku" in o}
    except Exception as e:  # network, auth, rate limit, bad JSON: the tool must keep working
        err = f"LLM unavailable ({type(e).__name__}), showing rule-based fallback."
        return [_fallback(s, c, err) for s, c in pairs]

    recs = []
    for s, c in pairs:
        try:
            recs.append(_finish(s, c, outs[s["sku"]]))
        except Exception:
            recs.append(_fallback(s, c, "LLM gave no usable answer for this SKU, showing rule-based fallback."))
    return recs


def _finish(s: dict, c: dict, out: dict) -> dict:
    price, clamped = clamp(out.get("price", s["our_price"]), c["min_price"], c["max_price"])
    if c["bucket"] == RECOVER:
        action = "lower"
    else:
        action = "hold" if out.get("action") == "hold" or price == s["our_price"] else "raise"
        if action == "hold":
            price = s["our_price"]

    # The AI's price stands, but a sentence with wrong numbers or vague advice is rebuilt from the final price.
    text = str(out.get("text", "")).strip()
    rewritten = clamped or not is_actionable(text, s, action, price)
    if rewritten:
        text = describe(s, action, price)
    else:  # LLMs round percentages loosely; the server's figure wins
        pct = (price - s["margin_floor"]) / price * 100
        text = re.sub(r"\d+(?:\.\d+)?% cushion", f"{pct:.1f}% cushion", text)
    return {"action": action, "price": price, "text": text, "source": "llm",
            "clamped": clamped, "rewritten": rewritten, "rule_flags": rule_violations(s, action, price)}
