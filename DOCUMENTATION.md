# ECI Platform — E-Commerce with Inventory
## Microservices-Based Application
### BITS Pilani — Scalable Services (S2-25_SEZG583)
### Problem Statement 4 | Semester 2, 2025

---

## Group Details

| Field | Details |
|---|---|
| **Institution** | BITS Pilani — Work Integrated Learning Programme |
| **Course** | Scalable Services (S2-25_SEZG583) |
| **Application Name** | ECI Platform (E-Commerce with Inventory) |
| **Problem Statement** | PS4 — E-commerce with Inventory Management |
| **Submission Date** | 03 May 2026 |

| Name | Student ID | Contribution |
|---|---|---|
| Atul Yadav | 2024TM93580 | 20% |
| Krishnadev S S | 2025TM93217 | 20% |
| Suraj Maurya | 2024TM93559 | 20% |
| P L S Phani Teja | 2024TM93573 | 20% |
| Vishnu Ganesan Senthil Kumar | 2025TM93193 | 20% |

---

## 1. Application Description

The **ECI Platform** is a production-grade microservices-based e-commerce system with multi-warehouse inventory management. It implements the full order lifecycle from product browsing through payment processing, inventory reservation, shipment tracking, and customer notification.

### Key Features
- **6 independent microservices**, each with its own database (database-per-service pattern)
- **Complete Place Order workflow**: Catalog pricing → Inventory reservation → Payment charge → Shipment creation → Notification
- **Idempotency** on all critical operations (orders and payments)
- **Atomic inventory reservations** across multiple warehouses with row-level locking
- **Reservation TTL reaper** — expired reservations auto-released after 15 minutes
- **SHA-256 totals signature** — tamper-proof order pricing
- **Banker's rounding** for financial calculations
- **Structured JSON logging** with PII masking (email, phone)
- **Prometheus metrics** on all services
- **Docker Compose** for local development
- **Kubernetes (Minikube)** for orchestrated deployment

### Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| Framework | FastAPI |
| ORM | SQLAlchemy (synchronous) |
| Database | SQLite (per service) |
| Schema Validation | Pydantic v2 |
| HTTP Client | httpx (synchronous) |
| Metrics | prometheus-client |
| Logging | python-json-logger |
| Scheduler | APScheduler (TTL reaper) |
| Containerization | Docker + Docker Compose |
| Orchestration | Kubernetes (Minikube) |

---

## 2. System Architecture

### 2.1 Component Diagram

![System Architecture](../System%20Architecture%20(Component%20Diagram).png)

The architecture follows a strict **database-per-service** pattern. Each service owns its data and communicates exclusively through REST APIs. No shared tables, no cross-database joins.

**Service Responsibilities:**

| Service | Port | Responsibility |
|---|---|---|
| Catalog Service | 8001 | Product CRUD, search, pricing |
| Inventory Service | 8002 | Stock levels, reservations, movements |
| Order Service | 8003 | Order orchestration (central coordinator) |
| Payment Service | 8004 | Charge, refund, idempotency |
| Shipping Service | 8005 | Shipment lifecycle tracking |
| Notification Service | 8006 | Event notifications (EMAIL/SMS) |

**Inter-Service Communication Pattern:**
- All communication is **synchronous REST** via HTTP (httpx)
- Order Service is the **orchestrator** — it calls all other services
- Services are **loosely coupled** — they hold only the data they need (e.g., OrderItems stores `product_name` and `unit_price` as a snapshot copied from Catalog at order time)

### 2.2 Project Structure (Layered Architecture)

Each service follows the same layered structure (analogous to Java Spring Boot):

```
{service}/
├── app/
│   ├── config.py        ← Settings via env vars  (like @ConfigurationProperties)
│   ├── database.py      ← SQLAlchemy engine + get_db()
│   ├── models.py        ← ORM models             (like @Entity)
│   ├── schemas.py       ← Pydantic DTOs           (like Request/Response DTOs)
│   ├── main.py          ← FastAPI app, middleware, lifespan
│   ├── routers/         ← Route handlers          (like @RestController)
│   └── services/        ← Business logic          (like @Service)
└── Dockerfile
```

---

## 3. Inter-Service Workflows

### 3.1 Place Order Flow (Reserve → Pay → Ship)

![Place Order Sequence Diagram](../place_order_sequence_diagram.png)

**Steps:**
1. Client sends `POST /v1/orders` with `Idempotency-Key` header and list of SKUs
2. Order Service checks idempotency — returns existing order if key already used
3. For each SKU, Order Service fetches authoritative pricing from **Catalog Service**
4. Order Service sends atomic reservation request to **Inventory Service**
   - Prefers fulfilling from a single warehouse; splits across warehouses if needed
   - Uses `SELECT ... FOR UPDATE` for atomic stock locking
   - Reservation TTL set to 15 minutes
5. Order total computed: `Σ(unit_price × qty) + 5% tax + ₹100 shipping` (Banker's rounding)
6. SHA-256 signature generated over pricing components to prevent tampering
7. Order Service charges **Payment Service** (idempotent, 90% success rate for CARD, 100% for COD)
8. On **SUCCESS**: Order confirmed → Shipment created → ORDER_CONFIRMED notification sent
9. On **FAILURE**: Inventory released → PAYMENT_FAILED notification sent → 402 returned

### 3.2 Cancellation Flow

| Scenario | Action |
|---|---|
| Cancel before payment | Release inventory reservations |
| Cancel after payment (CARD) | Release inventory + Refund payment → status = REFUNDED |
| Cancel after payment (COD) | Release inventory only |

### 3.3 Fulfillment Flow

Shipment status transitions: `PENDING → PACKED → SHIPPED → DELIVERED` (or `CANCELLED`)

### 3.4 Reservation TTL Reaper

A background APScheduler job runs every 5 minutes inside Inventory Service. It queries for reservations where `expires_at ≤ NOW` and `is_released = FALSE`, releases the reserved stock, and logs the release as an `INVENTORY_MOVEMENT` of type `RELEASE`.

---

## 4. Database Schema (Per Service)

### 4.1 Catalog DB

![Catalog DB Schema](../catalog_db.png)

### 4.2 Order DB

![Order DB Schema](../order_db.png)

### 4.3 Inventory DB

![Inventory DB Schema](../inventory_db.png)

### 4.4 Payment DB

![Payment DB Schema](../payments_db.png)

### 4.5 Shipping DB

![Shipping DB Schema](../shipping_db.png)

### 4.6 Notification DB

![Notification DB Schema](../notification_db.png)

---

## 5. API Reference

All services expose versioned APIs at `/v1`. OpenAPI 3.0 documentation is available at `{service-url}/docs`.

### Catalog Service — `/v1/products`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/v1/products` | List products (paginated, filterable by search/category/price) |
| POST | `/v1/products` | Create new product |
| GET | `/v1/products/sku/{sku}` | Get product by SKU |
| GET | `/v1/products/{id}` | Get product by ID |
| PUT | `/v1/products/{id}` | Update product |
| DELETE | `/v1/products/{id}` | Deactivate product |

### Inventory Service — `/v1/inventory`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/v1/inventory` | List stock levels (paginated) |
| GET | `/v1/inventory/product/{id}` | Stock by product across all warehouses |
| POST | `/v1/inventory/reserve` | Atomic multi-warehouse reservation |
| POST | `/v1/inventory/release` | Release reservation |
| POST | `/v1/inventory/ship` | Convert reservation to shipped |
| POST | `/v1/inventory/restock` | Add stock to warehouse |
| GET | `/v1/inventory/movements` | Audit log of all movements |

### Order Service — `/v1/orders`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/v1/orders` | List orders (paginated, filterable) |
| POST | `/v1/orders` | Place order (requires `Idempotency-Key` header) |
| GET | `/v1/orders/{id}` | Get order with items |
| POST | `/v1/orders/{id}/cancel` | Cancel order (triggers refund + inventory release) |

### Payment Service — `/v1/payments`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/v1/payments` | List payments (paginated) |
| POST | `/v1/payments/charge` | Charge payment (idempotent) |
| GET | `/v1/payments/{id}` | Get payment |
| POST | `/v1/payments/{id}/refund` | Refund payment (idempotent) |

### Shipping Service — `/v1/shipments`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/v1/shipments` | List shipments |
| POST | `/v1/shipments` | Create shipment |
| GET | `/v1/shipments/{id}` | Get shipment |
| PATCH | `/v1/shipments/{id}/status` | Update status (PACKED/SHIPPED/DELIVERED/CANCELLED) |

### Notification Service — `/v1/notifications`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/v1/notifications` | List notifications |
| POST | `/v1/notifications` | Send/log notification |
| GET | `/v1/notifications/{id}` | Get notification |

### Standard Error Response Format

All services return errors in this format:
```json
{
  "error": {
    "code": "404",
    "message": "Order 999 not found",
    "correlationId": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

### 5.1 Swagger UI — Catalog Service

![Swagger UI — Catalog Service](screenshots/swagger_catalog.png)

### 5.2 Swagger UI — Order Service

![Swagger UI — Order Service](screenshots/swagger_order.png)

### 5.3 Swagger UI — Inventory Service

![Swagger UI — Inventory Service](screenshots/swagger_inventory.png)

---

## 6. Monitoring & Observability

### 6.1 Prometheus Metrics

| Metric | Service | Type | Description |
|---|---|---|---|
| `orders_placed_total` | Order | Counter | Successfully placed orders |
| `orders_cancelled_total` | Order | Counter | Cancelled orders |
| `orders_failed_total` | Order | Counter | Failed orders |
| `payments_charged_total` | Payment | Counter | Successful charges |
| `payments_failed_total` | Payment | Counter | Failed payment attempts |
| `payments_refunded_total` | Payment | Counter | Refunds issued |
| `inventory_reserve_latency_ms` | Inventory | Histogram | Reservation latency |
| `stockouts_total` | Inventory | Counter | Out-of-stock events |
| `inventory_reservations_total` | Inventory | Counter | Reservations created |
| `shipments_created_total` | Shipping | Counter | Shipments created |
| `shipments_delivered_total` | Shipping | Counter | Shipments delivered |

Metrics are scraped at `GET /metrics` on each service (Prometheus text format).

**Screenshot — Order Service Metrics (`/metrics`):**

![Order Service Metrics](screenshots/metrics_order.png)

**Screenshot — Inventory Service Metrics (`/metrics`):**

![Inventory Service Metrics](screenshots/metrics_inventory.png)

**Screenshot — Payment Service Metrics (`/metrics`):**

![Payment Service Metrics](screenshots/metrics_payment.png)

### 6.2 Structured JSON Logging

All services use `python-json-logger` for structured JSON logs:

```json
{
  "asctime": "2026-05-02 03:42:20,734",
  "levelname": "INFO",
  "name": "order",
  "message": "Order confirmed: id=401 total=3775.96"
}
```

**PII masking** is applied before logging:
- Email: `at***@gmail.com`
- Phone: `*******9012`

**Screenshot — Structured JSON Logs (Order Service):**

![Order Service Logs](screenshots/logs_order_service.png)

---

## 7. Containerization (Docker)

### 7.1 Running with Docker Compose

```bash
# Start all 6 services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f order-service
```

### 7.2 Screenshot — `docker-compose up -d`

![Docker Compose Up](screenshots/docker_compose_up.png)

### 7.3 Screenshot — `docker ps` (All 6 Containers Running)

![Docker PS](screenshots/docker_ps.png)

### 7.4 Docker Compose Architecture

Each service has:
- Its own `Dockerfile` (Python 3.11-slim base)
- Named volume for SQLite DB persistence
- Read-only mount of `./data` for seed CSVs
- Health check on `/health` endpoint
- Environment variables for inter-service URLs

```yaml
# Example (order-service)
order-service:
  build: ./order-service
  ports: ["8003:8003"]
  volumes:
    - order-data:/db
    - ./data:/data:ro
  environment:
    - DATABASE_URL=sqlite:////db/order.db
    - CATALOG_SERVICE_URL=http://catalog-service:8001
    - INVENTORY_SERVICE_URL=http://inventory-service:8002
    - PAYMENT_SERVICE_URL=http://payment-service:8004
```

### 7.5 Health Check Endpoints

```bash
curl http://localhost:8001/health  # {"status":"healthy","service":"catalog-service"}
curl http://localhost:8002/health  # {"status":"healthy","service":"inventory-service"}
curl http://localhost:8003/health  # {"status":"healthy","service":"order-service"}
curl http://localhost:8004/health  # {"status":"healthy","service":"payment-service"}
curl http://localhost:8005/health  # {"status":"healthy","service":"shipping-service"}
curl http://localhost:8006/health  # {"status":"healthy","service":"notification-service"}
```

**Screenshot — Health Check Responses:**

![Health Checks](screenshots/docker_health_checks.png)

---

## 8. Kubernetes Deployment (Minikube)

### 8.1 Prerequisites

```bash
brew install minikube kubectl
```

### 8.2 Deployment Steps

```bash
# 1. Clone the repository
git clone https://github.com/atul411/eci-platform.git
cd eci-platform

# 2. Copy seed CSVs into Minikube VM
minikube start --driver=docker --cpus=4 --memory=4096
for f in data/*.csv; do
  minikube cp "$f" "/eci-data/$(basename $f)"
done

# 3. Build images inside Minikube and deploy
bash k8s/deploy.sh
```

### 8.3 Screenshot — Minikube Start

![Minikube Start](screenshots/minikube_start.png)

### 8.4 Kubernetes Manifest Structure

Each service has the following K8s resources:

| Resource | Purpose |
|---|---|
| `Namespace: eci` | Isolated namespace for all services |
| `ConfigMap: eci-config` | Shared environment variables (service URLs) |
| `Secret: eci-secrets` | Sensitive configuration keys |
| `Deployment` | Pod spec with readiness/liveness probes and resource limits |
| `PersistentVolumeClaim` | SQLite DB storage (1Gi per service) |
| `Service (ClusterIP)` | Internal service discovery |
| `Service (NodePort)` | External access on ports 30001–30006 |

### 8.5 Resource Limits (per pod)

```yaml
resources:
  requests:
    cpu: "100m"
    memory: "128Mi"
  limits:
    cpu: "500m"
    memory: "512Mi"
```

### 8.6 Screenshot — `kubectl get pods -n eci` (All Pods Running)

![kubectl get pods](screenshots/k8s_get_pods.png)

### 8.7 Screenshot — `kubectl get svc -n eci`

![kubectl get svc](screenshots/k8s_get_svc.png)

### 8.8 Accessing Services on Minikube

```bash
# Port-forward all services to localhost
kubectl port-forward -n eci svc/catalog-service      8001:8001 &
kubectl port-forward -n eci svc/inventory-service    8002:8002 &
kubectl port-forward -n eci svc/order-service        8003:8003 &
kubectl port-forward -n eci svc/payment-service      8004:8004 &
kubectl port-forward -n eci svc/shipping-service     8005:8005 &
kubectl port-forward -n eci svc/notification-service 8006:8006 &
```

---

## 9. Running the Demo

### 9.1 Full API Workflow Demo

```bash
# Runs all steps: health checks, CRUD, Place Order, Cancel, Metrics
bash demo.sh
```

**Screenshot — demo.sh Output:**

![Demo Script Output](screenshots/demo_sh_output.png)

### 9.2 CRUD — Create Product (Catalog Service)

```bash
curl -X POST http://localhost:8001/v1/products \
  -H "Content-Type: application/json" \
  -d '{"sku":"SKU-TEST","name":"Test Product","category":"Electronics","price":999.00}'
```

**Screenshot — Create Product Response:**

![Create Product](screenshots/crud_create_product.png)

### 9.3 CRUD — List Products

```bash
curl http://localhost:8001/v1/products?page=1&size=5
```

**Screenshot — List Products Response:**

![List Products](screenshots/crud_list_products.png)

### 9.4 Place Order (Reserve → Pay → Ship → Notify)

```bash
curl -X POST http://localhost:8003/v1/orders \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: my-unique-key-001" \
  -d '{
    "customer_id": 1,
    "payment_method": "COD",
    "items": [
      {"sku": "SKU1", "quantity": 1},
      {"sku": "SKU2", "quantity": 2}
    ]
  }'
```

**Screenshot — Place Order Response (CONFIRMED):**

![Place Order Response](screenshots/place_order_response.png)

### 9.5 Cancel Order

```bash
curl -X POST http://localhost:8003/v1/orders/401/cancel
```

**Screenshot — Cancel Order Response (REFUNDED):**

![Cancel Order Response](screenshots/cancel_order_response.png)

### 9.6 Check Metrics

```bash
curl http://localhost:8003/metrics | grep orders_
curl http://localhost:8004/metrics | grep payments_
curl http://localhost:8002/metrics | grep inventory_
```

**Screenshot — Metrics Output:**

![Metrics Output](screenshots/metrics_output.png)

### 9.7 Inventory Movements Audit Log

```bash
curl http://localhost:8002/v1/inventory/movements
```

**Screenshot — Inventory Movements:**

![Inventory Movements](screenshots/inventory_movements.png)

---

## 10. OpenAPI Specification

OpenAPI 3.1.0 specifications are auto-generated by FastAPI from the Pydantic schemas and route definitions. They describe every endpoint, request body, response model, and data schema for each service.

### 10.1 Interactive Swagger UI (Runtime)

Available at `/docs` on each running service:

| Service | Swagger UI | Raw Spec |
|---|---|---|
| Catalog | http://localhost:8001/docs | http://localhost:8001/openapi.json |
| Inventory | http://localhost:8002/docs | http://localhost:8002/openapi.json |
| Order | http://localhost:8003/docs | http://localhost:8003/openapi.json |
| Payment | http://localhost:8004/docs | http://localhost:8004/openapi.json |
| Shipping | http://localhost:8005/docs | http://localhost:8005/openapi.json |
| Notification | http://localhost:8006/docs | http://localhost:8006/openapi.json |

### 10.2 Static OpenAPI Spec Files (Repository)

Exported JSON spec files are committed to the repository under `openapi-specs/` and can be imported directly into Postman, Insomnia, or any API tool:

| Service | Spec File |
|---|---|
| Catalog | [catalog-service.json](https://github.com/atul411/eci-platform/blob/master/openapi-specs/catalog-service.json) |
| Inventory | [inventory-service.json](https://github.com/atul411/eci-platform/blob/master/openapi-specs/inventory-service.json) |
| Order | [order-service.json](https://github.com/atul411/eci-platform/blob/master/openapi-specs/order-service.json) |
| Payment | [payment-service.json](https://github.com/atul411/eci-platform/blob/master/openapi-specs/payment-service.json) |
| Shipping | [shipping-service.json](https://github.com/atul411/eci-platform/blob/master/openapi-specs/shipping-service.json) |
| Notification | [notification-service.json](https://github.com/atul411/eci-platform/blob/master/openapi-specs/notification-service.json) |

---

## 11. GitHub Repository Links

| Service | Repository |
|---|---|
| All Services (Monorepo) | https://github.com/atul411/eci-platform |
| Catalog Service | https://github.com/atul411/eci-platform/tree/master/catalog-service |
| Inventory Service | https://github.com/atul411/eci-platform/tree/master/inventory-service |
| Order Service | https://github.com/atul411/eci-platform/tree/master/order-service |
| Payment Service | https://github.com/atul411/eci-platform/tree/master/payment-service |
| Shipping Service | https://github.com/atul411/eci-platform/tree/master/shipping-service |
| Notification Service | https://github.com/atul411/eci-platform/tree/master/notification-service |

---

*ECI Platform — Built with FastAPI, SQLAlchemy, Docker, and Kubernetes*
