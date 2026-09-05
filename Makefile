# LedgerLock. `make demo` is the promise in the README: one command, seed 42,
# exactly the numbers in results.md.
PY := PYTHONPATH=src python3
SEED ?= 42

.PHONY: demo data check test run web clean

demo: data check test run   ## the whole thing, from an empty data/ to results.md

data:            ## regenerate the three CSVs + hidden ground truth
	$(PY) data/generate.py --seed $(SEED)

check:           ## prove all 11 planted edge cases survived generation
	$(PY) data/assert_planted.py

test:            ## exercise the paths the dataset itself does not reach
	$(PY) tests/test_tiers.py
	$(PY) tests/test_verifier.py
	$(PY) tests/test_analyst.py

run:             ## match, score, and write results.md + exceptions.md
	$(PY) run.py --seed $(SEED)

web:             ## the viewer -- http://127.0.0.1:8000 (needs `uv sync` once)
	PYTHONPATH=src uv run python -m web.server

clean:
	rm -f data/orders.csv data/settlements.csv data/bank_statement.csv \
	      data/ground_truth.csv data/fee_schedule.json \
	      results.md exceptions.md runs/run_log.jsonl
