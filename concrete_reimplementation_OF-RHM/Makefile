.PHONY: install verify-env run-controller run-topo run-s3-prng-fixed run-s3-csprng-fixed run-s3-dead-jitter run-s3-prng-jitter run-s3-all run-s2 run-s4c run-s1 run-all-experiments run-phase3-verify clean

# --- Setup ---

install:
	pip3 install --break-system-packages os-ken flask pydantic requests pyyaml fastapi uvicorn cryptography

verify-env:
	@echo "=== Checking OVS ==="
	sudo ovs-vsctl show
	@echo ""
	@echo "=== Checking Mininet ==="
	sudo mn --test pingall
	@echo ""
	@echo "=== Checking Ryu/os-ken ==="
	ryu-manager --version
	@echo ""
	@echo "=== All checks passed ==="

# --- Services ---

run-controller:
	OFRHM_REUSE_TIMEOUT=10 ryu-manager controller/ofrhm_app.py --verbose

run-topo:
	sudo python3 -m topology.ofrhm_topo --n 3

run-dead:
	python3 -m uvicorn dead.server:app --host 127.0.0.1 --port 8000

# --- Phase 3 Verification ---

run-phase3-verify:
	sudo python3 experiments/phase3_verify.py

# --- S3 Network Timing Experiments (Enhanced: fixed + jitter conditions) ---

run-s3-prng-fixed:
	sudo python3 experiments/s3_network_timing.py --provider prng --no-jitter --mutations 100

run-s3-csprng-fixed:
	sudo python3 experiments/s3_network_timing.py --provider csprng --no-jitter --mutations 100

run-s3-dead-jitter:
	sudo python3 experiments/s3_network_timing.py --provider dead --use-jitter --mutations 100

run-s3-prng-jitter:
	sudo python3 experiments/s3_network_timing.py --provider prng --use-jitter --mutations 100

run-s3-all:
	sudo python3 experiments/s3_network_timing.py --run-all-conditions --mutations 100

# --- S2 Collision Experiments ---

run-s2:
	python3 experiments/s2_network_collision.py --replicas 5 --steps 1000

run-s2-quick:
	python3 experiments/s2_network_collision.py --replicas 5 --steps 500

run-s2-mininet:
	sudo python3 experiments/s2_network_collision.py --run-in-mininet --replicas 5 --steps 500

# --- S4c Clone Schedule Timing Experiments ---

run-s4c:
	python3 experiments/s4c_network_timing.py --run-all --trials 50

# --- S1 Boot-Time Entropy Prediction Experiments ---

run-s1:
	python3 experiments/s1_network_prediction.py --run-all --hosts 5 --rounds 3 --trials 10

# --- Run All Experiments ---

run-all-experiments: run-s2 run-s4c run-s1
	@echo ""
	@echo "=== S3 requires Mininet (sudo) — run separately: make run-s3-all ==="
	@echo "=== All non-Mininet experiments complete ==="

# --- Cleanup ---

clean:
	rm -rf results/*.json results/*.pcap
	rm -f /tmp/ofrhm_vip_state.json /tmp/ryu_*.log /tmp/dead*.log /tmp/dns_server.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name '*.pyc' -delete 2>/dev/null || true

clean-mininet:
	sudo mn -c
