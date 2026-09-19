# Everything runs offline against synthetic data. No credentials, no cloud.
#
# `make demo` is the entry point: it runs the whole pipeline end to end on the
# small profile and prints the results table. If it breaks, nothing else here
# is worth looking at.

PY := .venv/bin/python
DBT := ../.venv/bin/dbt
PROFILE ?= demo

.PHONY: setup data load dbt pipeline semantic dq charts test lint types demo dashboard dagster clean

setup:  ## Create the virtualenv and install the project
	uv venv --python 3.11
	uv pip install -e ".[dev]"
	.venv/bin/pre-commit install || true

data:  ## Generate the synthetic source systems
	WDP_PROFILE=$(PROFILE) $(PY) -m workplace_platform.gen.run

load:  ## Load data/raw into DuckDB under contract
	WDP_PROFILE=$(PROFILE) $(PY) -m workplace_platform.warehouse.loader

dbt:  ## Build the warehouse and run every dbt test
	cd dbt && DBT_PROFILES_DIR=. $(DBT) build

semantic:  ## Compile the metric registry, then check it against the warehouse
	$(PY) -m workplace_platform.semantic.compile
	$(PY) -m workplace_platform.semantic.validate

validate-metrics:  ## Registry-versus-warehouse check on its own
	$(PY) -m workplace_platform.semantic.validate

dq:  ## Run the data quality suite and score defect recall
	WDP_PROFILE=$(PROFILE) $(PY) -m workplace_platform.dq.runner

charts:  ## Write the README charts to docs/img/
	WDP_PROFILE=$(PROFILE) $(PY) -m workplace_platform.reporting.charts

pipeline: data load dbt semantic dq charts  ## The full pipeline

demo:  ## Full pipeline on the small profile, with the results table
	$(MAKE) pipeline PROFILE=demo
	WDP_PROFILE=demo $(PY) -m workplace_platform.reporting.results

dashboard:  ## Streamlit: metric catalogue, lineage, DQ scorecard
	.venv/bin/streamlit run app/streamlit_app.py

dagster:  ## Dagster UI for the asset graph
	.venv/bin/dagster dev -m workplace_platform.orchestration.definitions

test:  ## Unit tests plus the end-to-end smoke test
	.venv/bin/pytest -q

lint:
	.venv/bin/ruff check src tests app
	.venv/bin/ruff format --check src tests app

types:
	.venv/bin/mypy src

clean:  ## Remove generated data, the warehouse and dbt artefacts
	rm -rf data/raw warehouse dbt/target dbt/logs out semantic/compiled
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
