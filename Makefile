up-rag:
	docker compose --profile rag up -d

down-rag:
	docker compose --profile rag down

up-mcp:
	docker compose --profile mcp up -d

down-mcp:
	docker compose --profile mcp down

ps:
	docker compose ps

logs-api:
	docker compose logs -f api
