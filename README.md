# Pricing Signal

A decision tool for Opptra's Category Ops. It shows which SKUs need a price change right now, gives a specific
AI recommendation that never goes below the margin floor, and lets you apply it in one click (simulated).

## Run locally

```bash
# 1. Backend (Python 3.10+). The OpenAI key is optional; without it, rule-based recommendations are used.
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add LLM_API_KEY here (Cerebras by default)
.venv/bin/uvicorn main:app --port 8787

# 2. Frontend (in a second terminal)
cd frontend && npm install && npm run dev

# 3. Open http://localhost:5173
```



## How it works

```
data.py      8 SKUs from the case study (hardcoded, as the brief suggests)
pricing.py   Rules: which bucket a SKU is in, and the allowed price range
llm.py       LLM picks a price inside that range and writes the sentence; output is clamped
main.py      FastAPI: GET /api/triage, POST /api/skus/{id}/apply, POST /api/reset
frontend/    React triage view: Act now → Opportunity → Flagged → Needs a human → Repriced → No action
```

The split matters: **rules decide what is allowed, the LLM decides what is best inside that range.**


| Situation                                  | Bucket                                    | Allowed price                 | Who decides         |
| ------------------------------------------ | ----------------------------------------- | ----------------------------- | ------------------- |
| Buy Box Lost, competitor ≥ floor           | Act now                                   | `[floor, competitor − 1]`     | LLM                 |
| Buy Box Won, competitor > our price        | Opportunity, or No action if not worth it | `[our price, competitor − 1]` | LLM (raise or hold) |
| Buy Box Lost, competitor < floor (SKU-007) | Flagged                                   | none                          | Rules, no action    |


Floor guardrails apply in three layers: the range goes into the prompt, the LLM's price is clamped to it, and
`/apply` refuses any price below floor.

Every recommendation has three parts: **what** to set, **why** (gap to competitor, gap to floor, Buy Box
effect), and the **tradeoff** (Rs. per unit given up or gained, % cushion over floor, buffer or tie risk):

> Set SKU-001 to Rs.1,189 — Rs.10 below competitor, Rs.139 above margin floor. Recovers Buy Box at 11.7%
> cushion over floor; gives up Rs.110 per unit versus today.

The server checks the LLM's sentence. It must start with the verb that matches the decision (Set / Raise /
Hold), quote the final price, the correct gap to the competitor, the correct gap to the floor and the correct
tradeoff figure (Rs. per unit gained or given up, or for a hold the most a raise could add), and it must
not use vague words ("consider", "mindful", …). If it fails, the AI's price is kept and the sentence is rebuilt
from the verified numbers, so the text can't contradict the Apply button. The % cushion is always overwritten
with the server's own calculation.

The brief's example says "8.4% margin", but true margin needs unit cost, which isn't in the data. The tool
reports *cushion over floor* instead, which is (price − floor) / price, and that is 11.7% for SKU-001.

## Edge cases handled

- **SKU-007, competitor below margin floor:** shown in its own amber "Flagged" section with the gap (Rs.21),
and there is no Apply button. Matching would sell below target margin. It is likely a loss-leader or a
stock clearance, so the right move is to watch it or escalate.
- **Competitor exactly at floor:** we can only match at floor. The recommendation says the Buy Box becomes a
tie and is not guaranteed.
- **Buy Box lost but we're already cheaper or equal:** price is not the cause (stock, fulfilment, seller
rating), so it goes to "Needs a human" and no price cut is suggested.
- **Our price already below floor:** flagged for review rather than repriced.
- **Won, but competitor at or below our price:** we win on non-price factors, so hold. Raising is risky.
- **Won with a tiny gap (SKU-002, SKU-004, Rs.11):** the LLM is told a raise must be worth it. A hold goes  
to "No action needed" instead of cluttering the view.
- **No API key, LLM error, or bad JSON:** falls back to the deterministic rule (Rs.10 undercut or raise),
and the UI badge shows which one is active.
- **Missing or invalid data** (zero or negative prices, unknown Buy Box status): goes to "Needs a human".
- **Double apply:** the backend returns 409. After applying, the Buy Box shows as "Pending next snapshot"
rather than pretending we won it.



## What I cut, and what's next

Cut and Next with 4 more hours: : CSV upload (hardcoded data, per the hint), real marketplace APIs, auth, persistence (state is in memory), price history, editing the price before applying.

Scheduled competitor polling with alerts , an approval step for large cuts (for example, more than 10% change in price).

## Design note

The hardest call was how much to trust the LLM. Letting it reason freely about price gives better language,
but a below-floor recommendation is an outright failure. So the LLM never sees an unbounded problem: the rules
compute the legal range and the LLM only optimises inside it. That keeps the AI doing real work (judging
whether a Rs.11 gap is worth a raise, sizing the undercut, explaining the tradeoff) while the floor stays a
hard constraint in code, not a hope in a prompt.