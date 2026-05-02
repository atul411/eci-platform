#!/usr/bin/env bash
# ECI Platform — API Demo Script
# Demonstrates the full Place Order workflow and all CRUD operations
# Usage: bash demo.sh [BASE_PORT_OFFSET]
set -e

BASE=${BASE_URL:-"http://localhost"}
C_URL="$BASE:8001"
I_URL="$BASE:8002"
O_URL="$BASE:8003"
P_URL="$BASE:8004"
S_URL="$BASE:8005"
N_URL="$BASE:8006"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

header() { echo -e "\n${BLUE}══════════════════════════════════════${NC}"; echo -e "${YELLOW}$1${NC}"; }
ok()     { echo -e "${GREEN}✓ $1${NC}"; }

# ── Health Checks ─────────────────────────────────────────────────────────────
header "1. Health Checks"
for svc in "Catalog:$C_URL" "Inventory:$I_URL" "Order:$O_URL" "Payment:$P_URL" "Shipping:$S_URL" "Notification:$N_URL"; do
  NAME="${svc%%:*}"; URL="${svc#*:}"
  STATUS=$(curl -sf "$URL/health" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'])")
  ok "$NAME: $STATUS"
done

# ── Catalog Service ───────────────────────────────────────────────────────────
header "2. Catalog Service — List Products (page 1)"
curl -s "$C_URL/v1/products?page=1&size=3" | python3 -m json.tool
ok "Products listed"

header "2b. Catalog Service — Search 'Mobile'"
curl -s "$C_URL/v1/products?search=Mobile&size=2" | python3 -m json.tool

header "2c. Catalog Service — Get Product by SKU"
curl -s "$C_URL/v1/products/sku/SKU1" | python3 -m json.tool

header "2d. Catalog Service — Create New Product"
NEW_PROD=$(curl -sf -X POST "$C_URL/v1/products" \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-DEMO-001","name":"Demo Laptop","category":"Electronics","price":49999.00}')
echo "$NEW_PROD" | python3 -m json.tool
NEW_PROD_ID=$(echo "$NEW_PROD" | python3 -c "import sys,json; print(json.load(sys.stdin)['product_id'])")
ok "Created product ID=$NEW_PROD_ID"

# ── Inventory Service ─────────────────────────────────────────────────────────
header "3. Inventory Service — Stock Levels"
curl -s "$I_URL/v1/inventory?size=3" | python3 -m json.tool

header "3b. Inventory — Stock for Product 1"
curl -s "$I_URL/v1/inventory/product/1" | python3 -m json.tool

# ── Payment Service ───────────────────────────────────────────────────────────
header "4. Payment Service — List Payments"
curl -s "$P_URL/v1/payments?size=3" | python3 -m json.tool

# ── Shipping Service ──────────────────────────────────────────────────────────
header "5. Shipping Service — List Shipments"
curl -s "$S_URL/v1/shipments?size=3" | python3 -m json.tool

# ── Place Order (Full Workflow) ────────────────────────────────────────────────
header "6. PLACE ORDER — Full Reserve→Pay→Ship Workflow"
IDEM_KEY="demo-order-$(date +%s)"
ORDER=$(curl -sf -X POST "$O_URL/v1/orders" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{
    "customer_id": 1,
    "payment_method": "CARD",
    "items": [
      {"sku": "SKU1", "quantity": 1},
      {"sku": "SKU2", "quantity": 2}
    ]
  }')
echo "$ORDER" | python3 -m json.tool
ORDER_ID=$(echo "$ORDER" | python3 -c "import sys,json; print(json.load(sys.stdin)['order_id'])" 2>/dev/null || echo "")

if [ -n "$ORDER_ID" ]; then
  ok "Order placed: ID=$ORDER_ID"

  # ── Idempotency: replay same request ──────────────────────────────────────
  header "6b. Idempotency — Replay Same Order Request"
  curl -sf -X POST "$O_URL/v1/orders" \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $IDEM_KEY" \
    -d '{"customer_id": 1, "items": [{"sku": "SKU1", "quantity": 1}]}' | python3 -m json.tool
  ok "Idempotent response returned (same order_id=$ORDER_ID)"

  # ── Inventory movements after order ───────────────────────────────────────
  header "6c. Inventory Movements for Order $ORDER_ID"
  curl -s "$I_URL/v1/inventory/movements?order_id=$ORDER_ID" | python3 -m json.tool

  # ── Shipment created ──────────────────────────────────────────────────────
  header "6d. Shipments for Order $ORDER_ID"
  curl -s "$S_URL/v1/shipments?order_id=$ORDER_ID" | python3 -m json.tool

  # ── Notifications sent ────────────────────────────────────────────────────
  header "6e. Notifications for Customer 1"
  curl -s "$N_URL/v1/notifications?recipient_id=1" | python3 -m json.tool

  # ── Update Shipment Status ────────────────────────────────────────────────
  SHIP_DATA=$(curl -sf "$S_URL/v1/shipments?order_id=$ORDER_ID")
  SHIP_ID=$(echo "$SHIP_DATA" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['shipment_id'])" 2>/dev/null || echo "")
  if [ -n "$SHIP_ID" ]; then
    header "6f. Update Shipment $SHIP_ID: PENDING → PACKED → SHIPPED → DELIVERED"
    curl -sf -X PATCH "$S_URL/v1/shipments/$SHIP_ID/status" \
      -H "Content-Type: application/json" -d '{"status":"PACKED"}' | python3 -m json.tool
    curl -sf -X PATCH "$S_URL/v1/shipments/$SHIP_ID/status" \
      -H "Content-Type: application/json" -d '{"status":"SHIPPED"}' | python3 -m json.tool
    curl -sf -X PATCH "$S_URL/v1/shipments/$SHIP_ID/status" \
      -H "Content-Type: application/json" -d '{"status":"DELIVERED"}' | python3 -m json.tool
    ok "Shipment delivered"
  fi
fi

# ── Cancel Order Demo ─────────────────────────────────────────────────────────
header "7. Cancel Order — Place then Cancel"
CANCEL_ORDER=$(curl -sf -X POST "$O_URL/v1/orders" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: cancel-demo-$(date +%s)" \
  -d '{"customer_id": 2, "payment_method": "COD", "items": [{"sku": "SKU3", "quantity": 1}]}')
echo "$CANCEL_ORDER" | python3 -m json.tool
CANCEL_ID=$(echo "$CANCEL_ORDER" | python3 -c "import sys,json; print(json.load(sys.stdin)['order_id'])" 2>/dev/null || echo "")

if [ -n "$CANCEL_ID" ]; then
  sleep 1
  CANCEL_RESULT=$(curl -sf -X POST "$O_URL/v1/orders/$CANCEL_ID/cancel" \
    -H "Content-Type: application/json")
  echo "$CANCEL_RESULT" | python3 -m json.tool
  ok "Order $CANCEL_ID cancelled — inventory released"
fi

# ── Metrics ───────────────────────────────────────────────────────────────────
header "8. Prometheus Metrics (Order Service)"
curl -s "$O_URL/metrics" | grep -E "^(orders_placed|orders_cancelled|orders_failed|# HELP)" | head -20

header "8b. Prometheus Metrics (Inventory Service)"
curl -s "$I_URL/metrics" | grep -E "^(inventory_|stockouts|# HELP)" | head -20

header "8c. Prometheus Metrics (Payment Service)"
curl -s "$P_URL/metrics" | grep -E "^(payments_|# HELP)" | head -20

# ── OpenAPI Spec ──────────────────────────────────────────────────────────────
header "9. OpenAPI 3.0 Spec (Catalog Service — first 20 lines)"
curl -s "$C_URL/openapi.json" | python3 -m json.tool | head -30

echo -e "\n${GREEN}══════════════════════════════════════${NC}"
echo -e "${GREEN}  Demo complete!${NC}"
echo -e "${GREEN}  Swagger UIs available at:${NC}"
for port_svc in "8001:Catalog" "8002:Inventory" "8003:Order" "8004:Payment" "8005:Shipping" "8006:Notification"; do
  PORT="${port_svc%%:*}"; SVC="${port_svc#*:}"
  echo -e "    ${YELLOW}http://localhost:$PORT/docs${NC} — $SVC Service"
done
echo -e "${GREEN}══════════════════════════════════════${NC}\n"
