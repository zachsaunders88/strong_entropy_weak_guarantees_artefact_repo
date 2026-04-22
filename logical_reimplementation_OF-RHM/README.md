# Logical OF-RHM Reimplementation

This repository contains a logical reimplementation of the OpenFlow Random Host Mutation (OF-RHM) moving target defense system.

## Components

- **Controller**: Manages vIP allocation, mutation scheduling, and DNS resolution.
- **Gateway**: Acts as an OpenFlow switch/proxy, performing address translation.
- **Mininet**: Network topology simulation.

## Directory Structure

- `controller/`: Controller service logic.
- `gateway/`: Gateway service logic.
- `mininet/`: Mininet topology scripts.
- `configs/`: Configuration files.
- `tests/`: Unit and integration tests.
- `docs/`: Documentation.
- `scripts/`: Helper scripts.
- `common/`: Shared utilities (config, logging).

## How to Run

### Prerequisites
- Python 3.9+
- Mininet (for topology simulation)

### Setup
```bash
make install
```

### Running the Controller
```bash
make run-controller
```

### Running the Gateway
```bash
make run-gateway
```

### Running Tests
```bash
make test
```
