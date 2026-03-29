.PHONY: venv test

venv:
	tox -e venv

test:
	tox -e tests
