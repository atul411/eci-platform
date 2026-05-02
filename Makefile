.PHONY: help build up down logs test-local k8s-deploy k8s-delete k8s-status

SERVICES = catalog-service inventory-service order-service payment-service shipping-service notification-service
DATA_DIR  = $(shell pwd)/data

help:
	@echo "ECI Platform — Available targets:"
	@echo "  make build         Build all Docker images"
	@echo "  make up            Start all services via docker-compose"
	@echo "  make down          Stop all services"
	@echo "  make logs          Tail all service logs"
	@echo "  make test-local    Run API smoke tests against localhost"
	@echo "  make k8s-setup     Setup Minikube and build images inside it"
	@echo "  make k8s-deploy    Deploy all services to Minikube"
	@echo "  make k8s-delete    Remove all K8s resources"
	@echo "  make k8s-status    Show pod/service status"

build:
	docker-compose build

up:
	docker-compose up -d
	@echo "Services starting... waiting 15s for health checks"
	@sleep 15
	docker-compose ps

down:
	docker-compose down

logs:
	docker-compose logs -f

# ── Minikube / Kubernetes ──────────────────────────────────────────────────────
k8s-setup:
	@echo "→ Starting Minikube"
	minikube start --driver=docker --cpus=4 --memory=4096
	@echo "→ Switching to Minikube Docker daemon"
	@echo "  Run: eval \$$(minikube docker-env)"
	@echo "→ Building images in Minikube Docker daemon"
	@eval $$(minikube docker-env) && \
		for svc in $(SERVICES); do \
			echo "Building $$svc..."; \
			docker build -t $$svc:latest ./$$svc; \
		done
	@echo "→ Mounting dataset into Minikube (runs in background)"
	minikube mount "$(DATA_DIR)":/eci-data &
	@sleep 2
	@echo "Dataset mounted at /eci-data inside Minikube"

k8s-deploy:
	kubectl apply -f k8s/configmap.yaml
	@for svc in catalog inventory order payment shipping notification; do \
		kubectl apply -f k8s/$$svc-deployment.yaml; \
	done
	@echo "→ Waiting for pods..."
	kubectl wait --for=condition=ready pod -l app --timeout=120s -n eci
	kubectl get pods -n eci
	kubectl get svc  -n eci

k8s-delete:
	kubectl delete namespace eci --ignore-not-found

k8s-status:
	@echo "── Pods ──────────────────────────────────"
	kubectl get pods -n eci -o wide
	@echo "── Services ──────────────────────────────"
	kubectl get svc -n eci

# ── Local smoke test ───────────────────────────────────────────────────────────
test-local:
	@echo "Running API smoke tests..."
	@bash demo.sh
