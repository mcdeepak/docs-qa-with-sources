# Shortcuts. The one command that starts everything is: docker compose up --build

.PHONY: up down reset test eval eval-live sweep logs

up:        ## start the whole system
	docker compose up --build

down:      ## stop it, keep the data
	docker compose down

reset:     ## stop it and delete all data
	docker compose down -v

test:      ## unit and integration tests
	docker compose run --rm api pytest -q

eval:      ## golden-set evaluation, offline
	docker compose run --rm api python eval/run_eval.py --verbose

sweep:     ## compare support thresholds on the dev split
	docker compose run --rm api python eval/run_eval.py --sweep

eval-live: ## evaluation through the running API, reports latency
	docker compose run --rm api python eval/run_eval.py --api http://api:8000

logs:
	docker compose logs -f api worker
