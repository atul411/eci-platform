#!/usr/bin/env bash
# ECI Platform — Minikube Deployment Script
# Run this script to deploy the full platform on Minikube
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="$ROOT_DIR/data"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}→ $1${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $1${NC}"; }

# ── 1. Start Minikube ─────────────────────────────────────────────────────────
info "Starting Minikube (driver=docker, 4 CPUs, 4GB RAM)"
minikube start --driver=docker --cpus=4 --memory=4096 || minikube status

# ── 2. Mount dataset ──────────────────────────────────────────────────────────
info "Mounting dataset directory into Minikube at /eci-data"
info "  Source: $DATA_DIR"
# Kill any existing mount first
pkill -f "minikube mount" 2>/dev/null || true
minikube mount "$DATA_DIR":/eci-data &
MOUNT_PID=$!
sleep 3
info "Dataset mounted (PID=$MOUNT_PID) — do not kill this process during deployment"

# ── 3. Build images inside Minikube Docker ────────────────────────────────────
info "Switching to Minikube Docker daemon"
eval "$(minikube docker-env)"

SERVICES=(catalog-service inventory-service order-service payment-service shipping-service notification-service)
for svc in "${SERVICES[@]}"; do
  info "Building $svc..."
  docker build -t "$svc:latest" "$ROOT_DIR/$svc"
done

# ── 4. Create namespace + ConfigMap + Secret ─────────────────────────────────
info "Creating namespace, config, and secrets"
kubectl apply -f "$SCRIPT_DIR/configmap.yaml"
kubectl apply -f "$SCRIPT_DIR/secret.yaml"

# ── 5. Deploy all services ────────────────────────────────────────────────────
for svc in catalog inventory order payment shipping notification; do
  info "Deploying $svc-service..."
  kubectl apply -f "$SCRIPT_DIR/${svc}-deployment.yaml"
done

# ── 6. Wait for pods to be ready ─────────────────────────────────────────────
info "Waiting for all pods to be Ready (timeout 120s)..."
kubectl rollout status deployment/catalog-service     -n eci --timeout=120s
kubectl rollout status deployment/inventory-service   -n eci --timeout=120s
kubectl rollout status deployment/payment-service     -n eci --timeout=120s
kubectl rollout status deployment/shipping-service    -n eci --timeout=120s
kubectl rollout status deployment/notification-service -n eci --timeout=120s
kubectl rollout status deployment/order-service       -n eci --timeout=120s

# ── 7. Print access URLs ──────────────────────────────────────────────────────
MINIKUBE_IP=$(minikube ip)
echo ""
info "All services deployed!"
echo -e "${GREEN}Access URLs (NodePort):${NC}"
echo "  catalog-service      : http://$MINIKUBE_IP:30001/docs"
echo "  inventory-service    : http://$MINIKUBE_IP:30002/docs"
echo "  order-service        : http://$MINIKUBE_IP:30003/docs"
echo "  payment-service      : http://$MINIKUBE_IP:30004/docs"
echo "  shipping-service     : http://$MINIKUBE_IP:30005/docs"
echo "  notification-service : http://$MINIKUBE_IP:30006/docs"

echo ""
info "To run the API demo against Minikube:"
echo "  BASE_URL=http://$MINIKUBE_IP bash $ROOT_DIR/demo.sh"
echo ""
info "Pod status:"
kubectl get pods -n eci -o wide
