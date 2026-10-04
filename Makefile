.PHONY: dev services-up migrate-up migrate-down migrate-status migrate-new sqlc-generate

PGSSLMODE ?= disable
UVICORN ?= .venv/bin/uvicorn

dev:
	$(UVICORN) main:app --app-dir src --reload

services-up:
	docker compose up -d postgres redis

migrate-up:
	PGSSLMODE="$(PGSSLMODE)" dbmate --no-dump-schema up

migrate-down:
	PGSSLMODE="$(PGSSLMODE)" dbmate --no-dump-schema rollback

migrate-status:
	PGSSLMODE="$(PGSSLMODE)" dbmate status

migrate-new:
	@test -n "$(MIGRATION)" || { echo 'Usage: make migrate-new MIGRATION=describe_change' >&2; exit 1; }
	dbmate new "$(MIGRATION)"

sqlc-generate:
	sqlc generate
