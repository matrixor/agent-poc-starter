up-rag:
	docker compose --profile rag up -d

down-rag:
	docker compose --profile rag down

up-mcp:
	docker compose --profile mcp up -d

down-mcp:
	docker compose --profile mcp down

up-tsg:
	docker compose --profile tsg up -d --build tsg-officer

down-tsg:
	docker compose --profile tsg down

ps:
	docker compose ps

logs-api:
	docker compose logs -f api

logs-tsg:
	docker compose logs -f tsg-officer
