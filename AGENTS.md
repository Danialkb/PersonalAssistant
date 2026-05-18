# AGENTS.md

Инструкции для coding agents, работающих с этим репозиторием.

## Область действия

Этот файл применяется ко всему проекту. Если в подпапках появятся более конкретные `AGENTS.md`, следуй самому близкому к изменяемым файлам файлу.

## Назначение проекта

`PersonalAssistant` - text-first CLI ассистент для рабочих задач. Сейчас основные домены:

- Jira: чтение задач, создание задач, смена статусов, комментарии и обновление полей.
- GitLab: загрузка Merge Request context и формирование структурированного code review через LLM.

Проект должен оставаться небольшим, прагматичным и расширяемым: новые интеграции добавляются отдельными клиентами, сервисами, агентами и инструментами без разрастания существующих доменных модулей.

## Технологии

- Python `>=3.13`.
- Package manager: `uv`.
- Source layout: код в `src/personal_assistant`, тесты в `tests`.
- Runtime зависимости: `httpx`, `pydantic-ai`, `pydantic-settings`, `rich`.
- Тесты: `pytest`.
- Форматирование и проверки через pre-commit: `black`, `isort`, `ruff`, `mypy`, `bandit`, `gitleaks`.

## Быстрый старт

```bash
uv sync
uv run assistant chat
uv run assistant "достань мои задачи в jira"
uv run pytest
```

Для локального запуска нужны переменные окружения в `.env` или shell environment. Не коммить реальные токены.

Минимум для Jira:

- `JIRA_BASE_URL`
- `JIRA_API_KEY`
- опционально `JIRA_EMAIL`, `JIRA_AUTH_MODE`, `JIRA_PROJECT_KEY`, `JIRA_DEFAULT_JQL`, `JIRA_ASSIGNEE`

Для GitLab:

- `GITLAB_BASE_URL`
- `GITLAB_TOKEN`

Для LLM-режима:

- `OPENAI_API_KEY`
- опционально `OPENAI_MODEL`

## Полезные команды

Запуск тестов:

```bash
uv run pytest
```

Запуск одного файла тестов:

```bash
uv run pytest tests/test_cli.py
```

Запуск CLI:

```bash
uv run assistant chat
uv run assistant "покажи мои задачи"
uv run jira "переведи CCO-123 в Code Review"
```

Pre-commit проверки:

```bash
uvx pre-commit run --all-files
```

## Архитектура

Основные слои:

- `settings.py` - конфигурация через `pydantic-settings`.
- `clients/` - низкоуровневые HTTP-клиенты внешних API. Здесь допустимы endpoint paths, auth headers и parsing API payloads.
- `tools/` - тонкие доменные функции для CLI/agents. Они собирают клиента, вызывают API и форматируют простые ответы.
- `services/` - orchestration над клиентами, когда одного API-вызова недостаточно или нужно собрать доменный контекст.
- `agents/` - LLM prompts, command schemas, fallback planning и agent orchestration.
- `assistant.py` - верхнеуровневый маршрутизатор доменных агентов.
- `cli.py` - terminal interface, chat loop, confirmation flow.
- `ui.py` - вывод в терминал через `rich`.

Правило размещения логики:

- HTTP details держи в `clients/`.
- Многошаговую загрузку данных держи в `services/`.
- Prompt engineering и structured output models держи в `agents/`.
- CLI parsing и interactive confirmation держи в `cli.py`.
- Форматирование terminal output держи рядом с доменным инструментом или в `ui.py`, если это общий UI.

## Текущие доменные границы

Jira:

- `clients/jira.py` отвечает за Jira REST API, auth modes, ADF parsing/serialization.
- `tools/jira.py` содержит public helper functions для поиска, создания, transition, комментариев и обновления полей.
- `agents/jira.py` содержит `JiraAgent`, `JiraCommand`, planner instructions и local fallback behavior.

GitLab:

- `clients/gitlab.py` отвечает за GitLab REST API и parsing dataclasses.
- `services/gitlab_mr.py` собирает контекст MR для review.
- `agents/gitlab.py` содержит review result models, prompt builder и `GitLabMRReviewAgent`.
- GitLab MR review сейчас только возвращает структурированный результат. Не добавляй posting comments/approval side effects без явного запроса.

## Правила изменений

- Сохраняй существующий стиль: dataclasses для API entities, Pydantic models для structured agent output.
- Не смешивай чтение внешних API, prompt building и CLI presentation в одном классе.
- Любая write-команда во внешнюю систему должна проходить через confirmation flow, если она доступна из CLI.
- Не меняй `.env`, `.venv`, `.idea`, cache files и сгенерированные `__pycache__`.
- Не логируй и не печатай токены, auth headers, API keys или полные секретные URLs.
- Не делай реальные сетевые вызовы в тестах. Используй stubs, fakes или monkeypatch.
- Не добавляй тяжелые абстракции заранее. Выделяй слой только если он уже нужен для тестируемости, повторного использования или сохранения доменных границ.
- Для новых интеграций повторяй структуру `client -> service/tools -> agent -> cli`, а не расширяй Jira/GitLab модули неподходящей логикой.

## Тестирование

Добавляй или обновляй тесты рядом с изменяемым поведением:

- CLI и confirmation flow: `tests/test_cli.py`.
- Jira tool/client behavior: `tests/test_tools.py` и профильные тесты клиента.
- GitLab API parsing/client behavior: `tests/test_gitlab_client.py`.
- GitLab MR review orchestration и prompt/result behavior: `tests/test_gitlab_review_agent.py`.

При изменениях в agents проверяй:

- fallback behavior без `OPENAI_API_KEY`;
- structured output models;
- сохранение context summary;
- отсутствие write side effects без подтверждения.

Минимальная проверка перед завершением задачи:

```bash
uv run pytest
```

Если запускаешь только subset тестов, явно укажи это в финальном сообщении.

## Работа с внешними API

- API clients должны возвращать typed dataclasses или понятные domain values, а не протаскивать raw JSON выше без причины.
- Ошибки HTTP должны сохранять полезные детали ответа, но не раскрывать секреты.
- URL base normalization держи внутри клиента.
- Для Jira description используй ADF helpers из `JiraClient`; не собирай ADF ad hoc в agents или CLI.
- Для GitLab project path используй существующее URL encoding поведение клиента.

## UX CLI

- CLI ассистент отвечает на языке пользователя.
- Для русскоязычных сценариев сохраняй русские user-facing сообщения.
- Read-only команды можно выполнять сразу.
- Write-команды должны показывать понятный preview и требовать подтверждения.
- `/exit` завершает chat, `/clear` очищает agent context.

## Code review агент

Если меняешь GitLab MR reviewer, придерживайся политики из `REVIEWER.md`:

- меньше комментариев, выше качество;
- фокус на correctness, architecture, security, edge cases и важных тестах;
- без nitpicking и generic comments;
- severity: `blocking`, `important`, `suggestion`, `praise`;
- recommendation: `approve`, `approve_with_suggestions`, `request_changes`.

## Git и рабочая копия

- В рабочем дереве могут быть чужие незакоммиченные изменения. Не откатывай их.
- Перед правками проверяй `git status --short`.
- Ограничивай diff файлами, нужными для задачи.
- Не запускай destructive git commands без явного запроса пользователя.
