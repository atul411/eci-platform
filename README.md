# ECI Platform — E-Commerce with Inventory Management

Microservices-based e-commerce platform with multi-warehouse inventory management.  
**BITS Pilani | Scalable Services (S2-25_SEZG583) | Problem Statement 4**

---

## Group Members

| Name | Student ID |
|---|---|
| Atul Yadav | 2024TM93580 |
| Krishnadev S S | 2025TM93217 |
| Suraj Maurya | 2024TM93559 |
| P L S Phani Teja | 2024TM93573 |
| Vishnu Ganesan Senthil Kumar | 2025TM93193 |

---

## Repositories

Each service has its own repository:

| Service | Repository |
|---|---|
| Catalog Service | https://github.com/atul411/catalog-service |
| Inventory Service | https://github.com/atul411/inventory-service |
| Order Service | https://github.com/atul411/order-service |
| Payment Service | https://github.com/atul411/payment-service |
| Shipping Service | https://github.com/atul411/shipping-service |
| Notification Service | https://github.com/atul411/notification-service |

This monorepo contains the full platform for running all services together via Docker Compose and Kubernetes.

| Service | Port | Responsibility |
|---|---|---|
| Catalog Service | 8001 | Product CRUD, search, pricing |
| Inventory Service | 8002 | Stock levels, reservations, movements |
| Order Service | 8003 | Order orchestration (central coordinator) |
| Payment Service | 8004 | Charge, refund, idempotency |
| Shipping Service | 8005 | Shipment lifecycle tracking |
| Notification Service | 8006 | Email/SMS event notifications |

---

## Tech Stack

- **Language:** Python 3.11
- **Framework:** FastAPI
- **ORM:** SQLAlchemy (synchronous)
- **Database:** SQLite (database-per-service)
- **Schema Validation:** Pydantic v2
- **Metrics:** Prometheus (`/metrics` on each service)
- **Logging:** Structured JSON with PII masking
- **Containerization:** Docker + Docker Compose
- **Orchestration:** Kubernetes (Minikube)

---

## Quick Start — Docker Compose

```bash
# Start all 6 services
docker-compose up -d

# Check status
docker-compose ps

# Health checks
curl http://localhost:8001/health
curl http://localhost:8002/health
curl http://localhost:8003/health
curl http://localhost:8004/health
curl http://localhost:8005/health
curl http://localhost:8006/health
```

---

## Place an Order

```bash
curl -X POST http://localhost:8003/v1/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: order-key-001" \
  -d '{
    "customer_id": 1,
    "payment_method": "COD",
    "items": [
      {"sku": "SKU1", "quantity": 1},
      {"sku": "SKU2", "quantity": 2}
    ]
  }'
```

---

## Run Demo Script

```bash
# Runs full workflow: health checks, CRUD, Place Order, Cancel, Metrics
bash demo.sh
```

---

## Kubernetes (Minikube)

```bash
# Deploy everything
bash k8s/deploy.sh

# Port-forward all services
kubectl port-forward -n eci svc/catalog-service      8001:8001 &
kubectl port-forward -n eci svc/inventory-service    8002:8002 &
kubectl port-forward -n eci svc/order-service        8003:8003 &
kubectl port-forward -n eci svc/payment-service      8004:8004 &
kubectl port-forward -n eci svc/shipping-service     8005:8005 &
kubectl port-forward -n eci svc/notification-service 8006:8006 &

# Verify pods
kubectl get pods -n eci
```

---

## API Docs (Swagger UI)

| Service | URL |
|---|---|
| Catalog | http://localhost:8001/docs |
| Inventory | http://localhost:8002/docs |
| Order | http://localhost:8003/docs |
| Payment | http://localhost:8004/docs |
| Shipping | http://localhost:8005/docs |
| Notification | http://localhost:8006/docs |

---

## Key Features

- **Place Order workflow:** Catalog pricing → Inventory reservation → Payment → Shipment → Notification
- **Idempotency** on orders and payments
- **Atomic inventory reservations** across multiple warehouses with row-level locking
- **Reservation TTL reaper** — expired reservations auto-released after 15 minutes
- **SHA-256 totals signature** — tamper-proof order pricing
- **Banker's rounding** for financial calculations
- **Structured JSON logging** with PII masking (email, phone)
- **Prometheus metrics** on all services
