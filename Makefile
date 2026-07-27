# Makefile for Gmail Keep-Alive project
.PHONY: install run test clean

install:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt

run:
	python3 keep_alive.py --config config.yaml

test:
	@echo "No test suite defined yet."

clean:
	rm -rf venv __pycache__ *.pyc