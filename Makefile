# Makefile for Gmail Keep-Alive project
.PHONY: install run test clean

install:
	python3 -m venv venv
	./venv/bin/pip install -r requirements.txt -r requirements-dev.txt

run:
	./venv/bin/python keep_alive.py --config config.yaml

test:
	./venv/bin/python -m pytest

clean:
	rm -rf venv __pycache__ *.pyc
