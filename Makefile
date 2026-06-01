PY ?= .venv/bin/python
PI ?= http://rpi.local:8080

.PHONY: help app camera brain test health stop install-pi install-brain

help:
	@echo "Pi:"
	@echo "  make app          Run motor controller (port 8080)"
	@echo "  make camera       Run MJPEG camera streamer (port 8081)"
	@echo "  make install-pi   Create .venv and install Pi requirements"
	@echo ""
	@echo "Mac:"
	@echo "  make brain        Run autopilot brain"
	@echo "  make find         Run object-find server"
	@echo "  make install-brain  Create .venv and install brain extras"
	@echo ""
	@echo "Either:"
	@echo "  make test         Run unit tests"
	@echo "  make health       curl PI /health"
	@echo "  make stop         curl PI /stop  (emergency)"

app:
	GPIOZERO_PIN_FACTORY=lgpio VALLE_DRIVER=gpiozero $(PY) -m valle.app

camera:
	$(PY) -m valle.camera

brain:
	$(PY) -m valle.brain

find:
	$(PY) -m valle.find

test:
	$(PY) -m unittest discover tests -v

health:
	curl -s $(PI)/health

stop:
	curl -s -X POST $(PI)/stop

install-pi:
	python3 -m venv --system-site-packages .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt

install-brain:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -e '.[brain]'
