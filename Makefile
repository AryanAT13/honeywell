PY := .venv/bin/python
ECOLOOP := .venv/bin/ecoloop

.PHONY: setup install-eplus info smoke baseline compare agent evidence serve inspector test lint clean

setup: install-eplus .venv

install-eplus:
	@bash scripts/install_energyplus.sh

.venv:
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -e ".[dev]"

info:
	@$(ECOLOOP) info

smoke:
	@$(ECOLOOP) run --label baseline --period smoke

baseline:
	@$(ECOLOOP) run --label baseline_annual --period annual

compare:
	@$(ECOLOOP) compare --arms baseline,deadband,supervisor --period annual

agent:
	@$(ECOLOOP) compare --arms baseline,supervisor,agent --period annual

evidence:
	@$(ECOLOOP) evidence

serve:
	@$(ECOLOOP) serve

inspector:
	@npx -y @modelcontextprotocol/inspector $(ECOLOOP) serve

test:
	@.venv/bin/pytest -q

lint:
	@.venv/bin/ruff check ecoloop tests
	@.venv/bin/ruff format --check ecoloop tests

clean:
	rm -rf runs .pytest_cache .ruff_cache
