REGISTRY ?= registry.ibis-silverside.ts.net
IMAGE ?= $(REGISTRY)/chart-analyzer-optimized
VERSION := $(shell cat VERSION)
NAMESPACE ?= chart-analyzer-optimized
RELEASE ?= chart-analyzer-optimized

.PHONY: test build push lint template deploy

test:
	python -m pytest -q

build:
	docker build -t $(IMAGE):$(VERSION) .

push:
	docker push $(IMAGE):$(VERSION)

lint:
	helm lint charts/chart-analyzer

template:
	helm template $(RELEASE) charts/chart-analyzer --namespace $(NAMESPACE) --set image.repository=$(IMAGE) --set-string image.tag=$(VERSION)

deploy:
	helm upgrade --install $(RELEASE) charts/chart-analyzer --namespace $(NAMESPACE) --create-namespace --set image.repository=$(IMAGE) --set-string image.tag=$(VERSION)
