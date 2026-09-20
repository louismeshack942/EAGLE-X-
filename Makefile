.PHONY: install test build dev

install:
	cd backend && pip install -r requirements.txt
	cd twin && npm install

test:
	cd backend && python -m pytest tests/ -q

build:
	cd twin && npm run build

dev-backend:
	cd backend && python -m uvicorn app.main:app --reload --port 8000

dev-frontend:
	cd twin && npm run dev
