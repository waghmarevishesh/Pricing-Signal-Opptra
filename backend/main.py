import copy
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

import llm  # noqa: E402
from data import SKUS  # noqa: E402
from pricing import HOLD, RAISE, RECOVER, classify, metrics  # noqa: E402

app = FastAPI(title="Opptra Pricing Signal")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# In-memory state is enough for a prototype; it resets on restart.
state = {"skus": {}, "cache": {}, "generated_at": None}


def reset_state():
    state["skus"] = {s["sku"]: {**copy.deepcopy(s), "status": "Open"} for s in SKUS}
    state["cache"] = {}
    state["generated_at"] = None


reset_state()


async def build_items(skus: list[dict]) -> list[dict]:
    items = [{**s, **classify(s), "recommendation": None} for s in skus]
    todo = [(s, item) for s, item in zip(skus, items) if item["bucket"] in (RECOVER, RAISE)]
    for (s, item), rec in zip(todo, await llm.recommend_all(todo)):
        rec["metrics"] = metrics(s, rec["price"])
        item["recommendation"] = rec
        if rec["action"] == "hold":
            item["bucket"] = HOLD
            item["reason"] = "Won Buy Box, but the gap is too small to be worth a raise."
    return items


@app.get("/api/triage")
async def triage(refresh: bool = False):
    if refresh:
        state["cache"] = {}
    todo = [s for sid, s in state["skus"].items() if s["status"] == "Open" and sid not in state["cache"]]
    for item in await build_items(todo):
        state["cache"][item["sku"]] = item
    if todo:
        state["generated_at"] = datetime.now().isoformat(timespec="seconds")
        state["reasoning"] = llm.last_reasoning

    items = [state["cache"].get(sid) or s for sid, s in state["skus"].items()]
    return {"generated_at": state["generated_at"], "llm_enabled": llm.enabled(), "model": llm.MODEL,
            "reasoning": state.get("reasoning"), "items": items}


@app.post("/api/skus/{sku_id}/apply")
def apply(sku_id: str):
    s = state["skus"].get(sku_id)
    item = state["cache"].get(sku_id)
    if s and s["status"] == "Repriced":
        raise HTTPException(409, "Already repriced.")
    if not s or not item or not item.get("recommendation"):
        raise HTTPException(404, "No recommendation to apply for this SKU.")
    new_price = item["recommendation"]["price"]
    if new_price < s["margin_floor"]:  # last line of defence
        raise HTTPException(422, "Refusing to price below the margin floor.")

    # Simulated marketplace update. The Buy Box result is only known at the next price snapshot.
    s.update(old_price=s["our_price"], our_price=new_price, status="Repriced", buy_box="Pending",
             last_changed="Just now", applied_at=datetime.now().isoformat(timespec="seconds"),
             recommendation=item["recommendation"])
    state["cache"].pop(sku_id, None)
    return s


@app.post("/api/reset")
def reset():
    reset_state()
    return {"ok": True}
