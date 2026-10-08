.PHONY: install run test
install:
	cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
run:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000
test:
	cd backend && .venv/bin/python -m pytest -q
