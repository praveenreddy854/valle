PY ?= .venv/bin/python
VENV_PY ?= python3.12
PI ?= http://rpi.local:8080

.PHONY: help app brain-api agent test health stop install-pi install-brain-api

help:
	@echo "Pi (one process: motor API 8080 + camera 8081):"
	@echo "  make app          Run the Pi control server and camera"
	@echo "  make install-pi   Create .venv and install Pi requirements"
	@echo ""
	@echo "Mac (one service: API 8090 with find/seek, agent, autopilot):"
	@echo "  make brain-api    Run the brain HTTP API"
	@echo "  make agent        Run a one-shot CrewAI mission from the CLI"
	@echo "  make install-brain-api  Create .venv and install brain API extras"
	@echo ""
	@echo "Either:"
	@echo "  make test         Run unit tests"
	@echo "  make health       curl PI /health"
	@echo "  make stop         curl PI /stop  (emergency)"

app:
	GPIOZERO_PIN_FACTORY=lgpio VALLE_DRIVER=gpiozero $(PY) -m valle.app

brain-api:
	$(PY) -m valle.brain.api

agent:
	$(PY) -m valle.brain.agent

test:
	$(PY) -m unittest discover tests -v

health:
	curl -s $(PI)/health

stop:
	curl -s -X POST $(PI)/stop

install-pi:
	$(VENV_PY) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else "Valle requires Python 3.12. Run: VENV_PY=python3.12 make install-pi")'
	$(VENV_PY) -m venv --system-site-packages .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

install-brain-api:
	$(VENV_PY) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else "Valle requires Python 3.12. Run: VENV_PY=python3.12 make install-brain-api")'
	$(VENV_PY) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e '.[brain,agent]'
