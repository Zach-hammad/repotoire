# Benchmark Suite for Repotoire Precision Testing
#
# Clones 5 OSS projects at pinned versions for reproducible benchmarking.
# Usage:
#   make setup    — clone all projects (shallow, pinned tags)
#   make run      — analyze all projects, save results as JSON
#   make clean    — remove cloned repos and results

# Pinned tags for reproducibility
FLASK_TAG    := 3.1.0
FASTAPI_TAG  := 0.115.6
TOKIO_TAG    := tokio-1.41.1
SERDE_TAG    := v1.0.215
EXPRESS_TAG  := v5.0.1

# Detect the repotoire-cli Cargo.toml relative to this Makefile.
# When invoked via `make -C benchmark` the CWD is benchmark/.
ifneq (,$(wildcard ../repotoire-cli/Cargo.toml))
  CARGO_MANIFEST := ../repotoire-cli/Cargo.toml
else ifneq (,$(wildcard repotoire-cli/Cargo.toml))
  CARGO_MANIFEST := repotoire-cli/Cargo.toml
else
  CARGO_MANIFEST := ../repotoire-cli/Cargo.toml
endif

REPOTOIRE := cargo run --release --manifest-path $(CARGO_MANIFEST) --

PROJECTS := flask fastapi tokio serde express

.PHONY: setup run clean $(PROJECTS)

setup: $(PROJECTS)
	@echo "All benchmark projects cloned."

flask:
	@if [ ! -d flask/repo ]; then \
		echo "Cloning Flask @ $(FLASK_TAG)..."; \
		git clone --depth 1 --branch $(FLASK_TAG) https://github.com/pallets/flask.git flask/repo; \
	else \
		echo "Flask already cloned."; \
	fi

fastapi:
	@if [ ! -d fastapi/repo ]; then \
		echo "Cloning FastAPI @ $(FASTAPI_TAG)..."; \
		git clone --depth 1 --branch $(FASTAPI_TAG) https://github.com/fastapi/fastapi.git fastapi/repo; \
	else \
		echo "FastAPI already cloned."; \
	fi

tokio:
	@if [ ! -d tokio/repo ]; then \
		echo "Cloning Tokio @ $(TOKIO_TAG)..."; \
		git clone --depth 1 --branch $(TOKIO_TAG) https://github.com/tokio-rs/tokio.git tokio/repo; \
	else \
		echo "Tokio already cloned."; \
	fi

serde:
	@if [ ! -d serde/repo ]; then \
		echo "Cloning Serde @ $(SERDE_TAG)..."; \
		git clone --depth 1 --branch $(SERDE_TAG) https://github.com/serde-rs/serde.git serde/repo; \
	else \
		echo "Serde already cloned."; \
	fi

express:
	@if [ ! -d express/repo ]; then \
		echo "Cloning Express @ $(EXPRESS_TAG)..."; \
		git clone --depth 1 --branch $(EXPRESS_TAG) https://github.com/expressjs/express.git express/repo; \
	else \
		echo "Express already cloned."; \
	fi

run: setup
	@echo "Running Repotoire analysis on all benchmark projects..."
	@for proj in $(PROJECTS); do \
		echo ""; \
		echo "=== Analyzing $$proj ==="; \
		$(REPOTOIRE) analyze $$proj/repo --format json --output $$proj/results.json; \
	done
	@echo ""
	@echo "All analyses complete. Results saved to <project>/results.json"

clean:
	@echo "Removing cloned repos and results..."
	@for proj in $(PROJECTS); do \
		rm -rf $$proj/repo $$proj/results.json; \
	done
	@echo "Clean complete."
