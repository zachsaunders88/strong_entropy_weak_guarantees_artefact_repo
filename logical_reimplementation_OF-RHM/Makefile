.PHONY: install test run-controller run-gateway clean help

PYTHON := python3

help:
	@echo "Available commands:"
	@echo "  make install         Install dependencies"
	@echo "  make test            Run tests"
	@echo "  make run-controller  Run the controller service"
	@echo "  make run-gateway     Run the gateway service"
	@echo "  make clean           Clean up artifacts"

install:
	pip install -r requirements.txt

test:
	$(PYTHON) -m pytest tests/

run-controller:
	$(PYTHON) -m controller --config configs/dev.yaml

run-gateway:
	$(PYTHON) -m gateway --config configs/dev.yaml

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
