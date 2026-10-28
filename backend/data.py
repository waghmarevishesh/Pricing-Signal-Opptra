"""9am snapshot from the case study. Prices are in INR.

`margin_floor` is the lowest price we can sell at and still hit the brand's
target margin, so no recommendation may go below it.
"""

SKUS = [
    {"sku": "SKU-001", "brand": "Natura Casa",   "our_price": 1299, "competitor_price": 1199, "buy_box": "Lost", "margin_floor": 1050, "last_changed": "3 days ago"},
    {"sku": "SKU-002", "brand": "Natura Casa",   "our_price": 849,  "competitor_price": 860,  "buy_box": "Won",  "margin_floor": 720,  "last_changed": "Today"},
    {"sku": "SKU-003", "brand": "LivSpace Pro",  "our_price": 2499, "competitor_price": 2199, "buy_box": "Lost", "margin_floor": 1800, "last_changed": "6 days ago"},
    {"sku": "SKU-004", "brand": "LivSpace Pro",  "our_price": 599,  "competitor_price": 610,  "buy_box": "Won",  "margin_floor": 480,  "last_changed": "2 days ago"},
    {"sku": "SKU-005", "brand": "Artisan Home",  "our_price": 3799, "competitor_price": 3750, "buy_box": "Lost", "margin_floor": 3200, "last_changed": "1 day ago"},
    {"sku": "SKU-006", "brand": "Artisan Home",  "our_price": 1150, "competitor_price": 1390, "buy_box": "Won",  "margin_floor": 900,  "last_changed": "Today"},
    {"sku": "SKU-007", "brand": "Nordic Basics", "our_price": 449,  "competitor_price": 399,  "buy_box": "Lost", "margin_floor": 420,  "last_changed": "5 days ago"},
    {"sku": "SKU-008", "brand": "Nordic Basics", "our_price": 2199, "competitor_price": 2100, "buy_box": "Lost", "margin_floor": 1750, "last_changed": "4 days ago"},
]
