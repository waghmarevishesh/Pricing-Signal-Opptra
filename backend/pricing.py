"""Deterministic triage + guardrails.

Rules decide *which* bucket a SKU is in and the allowed price range.
The LLM only picks a price inside that range and explains it. Whatever the
LLM returns is clamped back into the range, so it can never go below floor.
"""

# Buckets, in the order the UI shows them.
RECOVER = "recover"          # Lost Buy Box, competitor >= floor -> undercut
RAISE = "raise"              # Won Buy Box, LLM decides whether to raise
BELOW_FLOOR = "below_floor"  # Lost Buy Box, competitor < floor -> no action
REVIEW = "review"            # Data / non-price problem, needs a human
HOLD = "hold"                # Nothing to do


def validate(s: dict) -> str | None:
    for key in ("our_price", "competitor_price", "margin_floor"):
        v = s.get(key)
        if not isinstance(v, (int, float)) or v <= 0:
            return f"Invalid or missing {key.replace('_', ' ')}."
    if s.get("buy_box") not in ("Won", "Lost"):
        return "Unknown Buy Box status."
    return None


def classify(s: dict) -> dict:
    """Return bucket, a short reason, and the allowed [min_price, max_price]."""
    error = validate(s)
    if error:
        return {"bucket": REVIEW, "reason": error}

    our, comp, floor = s["our_price"], s["competitor_price"], s["margin_floor"]

    if our < floor:
        return {"bucket": REVIEW,
                "reason": f"Our price Rs.{our:,} is already below the margin floor Rs.{floor:,}. Check the floor or raise to Rs.{floor:,}."}

    if s["buy_box"] == "Lost":
        if comp < floor:
            return {"bucket": BELOW_FLOOR,
                    "reason": f"Competitor at Rs.{comp:,} is Rs.{floor - comp:,} below our floor Rs.{floor:,}. Matching would break margin, so hold the price and watch. Possibly a loss-leader or a stock clearance."}
        if our <= comp:
            return {"bucket": REVIEW,
                    "reason": f"We are already at or below the competitor (Rs.{our:,} vs Rs.{comp:,}) and still lost the Buy Box. Price is not the cause; check stock, fulfilment or seller rating."}
        # Undercut by at least Rs.1, but never below floor. If competitor == floor,
        # the best we can do is match at floor.
        return {"bucket": RECOVER, "min_price": floor, "max_price": max(floor, comp - 1),
                "reason": f"Lost Buy Box, Rs.{our - comp:,} above competitor. Can undercut within margin."}

    # Buy Box Won
    if comp <= our:
        return {"bucket": HOLD,
                "reason": f"Winning even though competitor is at or below us (Rs.{comp:,}). Raising risks the Buy Box."}
    return {"bucket": RAISE, "min_price": our, "max_price": comp - 1,
            "reason": f"Won Buy Box with Rs.{comp - our:,} gap to competitor."}


def clamp(price, lo: int, hi: int) -> tuple[int, bool]:
    p = int(round(float(price)))
    c = min(max(p, lo), hi)
    return c, c != p


def metrics(s: dict, price: int) -> dict:
    floor, comp = s["margin_floor"], s["competitor_price"]
    return {
        "above_floor": price - floor,
        "cushion_pct": round((price - floor) / price * 100, 1),
        "vs_competitor": price - comp,
        "change": price - s["our_price"],
    }


def rule_recommendation(s: dict, c: dict) -> dict:
    """Fallback used when no LLM key is set or the LLM call fails."""
    our, comp, floor = s["our_price"], s["competitor_price"], s["margin_floor"]

    if c["bucket"] == RECOVER:
        # Rs.10 undercut, or less if the floor is closer. If competitor == floor, this matches at floor.
        action, price = "lower", comp - min(10, comp - floor)
    elif comp - our >= max(20, our * 0.02):  # raise only if the gap is meaningful
        action, price = "raise", comp - 10
    else:
        action, price = "hold", our
    return {"action": action, "price": price, "text": describe(s, action, price)}


MIN_RAISE_PCT = 2      # a raise must add at least this % of our_price per unit
BUFFER_PCT = 1         # and leave about this % of the competitor price as a buffer
MIN_BUFFER = 5         # but never less than Rs.5


def rule_violations(s: dict, action: str, price: int) -> list[str]:
    """Basic raise rules the AI was told to follow. Reported for review, never auto-corrected."""
    if action != "raise":
        return []
    our, comp = s["our_price"], s["competitor_price"]
    issues = []
    gain_pct = (price - our) / our * 100
    if gain_pct < MIN_RAISE_PCT:
        issues.append(f"Adds Rs.{price - our:,} ({gain_pct:.1f}%), under the {MIN_RAISE_PCT}% minimum for a raise.")
    min_buffer = max(MIN_BUFFER, round(comp * BUFFER_PCT / 100))
    if comp - price < min_buffer:
        issues.append(f"Leaves Rs.{comp - price:,} under the competitor, below the Rs.{min_buffer:,} buffer rule.")
    return issues


def required_numbers(s: dict, action: str, price: int) -> set[int]:
    """Numbers a recommendation sentence must quote: price, gap to competitor, gap to floor,
    and the tradeoff (per-unit change, or for a hold the most a raise could add)."""
    our, comp = s["our_price"], s["competitor_price"]
    tradeoff = max(comp - our - 1, 0) if action == "hold" else abs(price - our)
    return {price, abs(comp - price), price - s["margin_floor"], tradeoff}


def describe(s: dict, action: str, price: int) -> str:
    """Build the recommendation sentence from the final, clamped numbers.
    Format: what to set, why (Buy Box effect), and the tradeoff."""
    sku, our, comp, floor = s["sku"], s["our_price"], s["competitor_price"], s["margin_floor"]
    pct = (price - floor) / price * 100
    if action == "hold":
        return (f"Hold {sku} at Rs.{our:,} — Rs.{comp - our:,} below competitor, Rs.{our - floor:,} above margin "
                f"floor. Keeps the Buy Box; a raise would add at most Rs.{max(comp - our - 1, 0):,} per unit but "
                f"leave almost no buffer if the competitor drops.")
    if action == "raise":
        return (f"Raise {sku} to Rs.{price:,} — Rs.{comp - price:,} below competitor, Rs.{price - floor:,} above "
                f"margin floor. Adds Rs.{price - our:,} per unit ({pct:.1f}% cushion over floor) while keeping the "
                f"Buy Box; the tradeoff is only a Rs.{comp - price:,} buffer if the competitor cuts.")
    if price >= comp:
        return (f"Set {sku} to Rs.{price:,} — Rs.0 below competitor, Rs.{price - floor:,} above margin floor. "
                f"Matches the competitor, the lowest we can go without breaking margin; gives up Rs.{our - price:,} "
                f"per unit and the Buy Box becomes a tie, not guaranteed.")
    return (f"Set {sku} to Rs.{price:,} — Rs.{comp - price:,} below competitor, Rs.{price - floor:,} above margin "
            f"floor. Recovers Buy Box at {pct:.1f}% cushion over floor; gives up Rs.{our - price:,} per unit "
            f"versus today.")
