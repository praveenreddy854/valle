PY ?= .venv/bin/python
VENV_PY ?= python3.12
PI ?= http://rpi.local:8080

.PHONY: help app camera brain brain-api find agent sim test health stop install-pi install-brain install-agent install-brain-api

help:
	@echo "Pi:"
	@echo "  make app          Run motor controller (port 8080)"
	@echo "  make camera       Run MJPEG camera streamer (port 8081)"
	@echo "  make install-pi   Create .venv and install Pi requirements"
	@echo ""
	@echo "Mac:"
	@echo "  make brain        Run autopilot brain"
	@echo "  make brain-api    Run brain HTTP API (port 8090)"
	@echo "  make find         Run brain object-find/seek server"
	@echo "  make agent        Run brain CrewAI inspection agent"
	@echo "  make install-brain  Create .venv and install brain extras"
	@echo "  make install-agent  Create Python 3.12 .venv and install CrewAI agent extras"
	@echo "  make install-brain-api  Create .venv and install brain API extras"
	@echo ""
	@echo "Either:"
	@echo "  make sim          Run the digital twin (Pi API 8080 + sim camera 8081)"
	@echo "  make test         Run unit tests"
	@echo "  make health       curl PI /health"
	@echo "  make stop         curl PI /stop  (emergency)"

app:
	GPIOZERO_PIN_FACTORY=lgpio VALLE_DRIVER=gpiozero $(PY) -m valle.app

camera:
	$(PY) -m valle.camera

brain:
	$(PY) -m valle.brain

brain-api:
	$(PY) -m valle.brain.api

find:
	$(PY) -m valle.brain.find

agent:
	$(PY) -m valle.brain.agent

sim:
	$(PY) -m valle.sim

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

install-brain:
	$(VENV_PY) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else "Valle requires Python 3.12. Run: VENV_PY=python3.12 make install-brain")'
	$(VENV_PY) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e '.[brain]'

install-agent:
	$(VENV_PY) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else "Valle requires Python 3.12. Run: VENV_PY=python3.12 make install-agent")'
	$(VENV_PY) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e '.[brain,agent]'

install-brain-api:
	$(VENV_PY) -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else "Valle requires Python 3.12. Run: VENV_PY=python3.12 make install-brain-api")'
	$(VENV_PY) -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e '.[brain,agent]'
