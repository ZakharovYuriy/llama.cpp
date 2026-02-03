# Незакоммиченные изменения (анализ)

**Общая картина**
- В индексе (staged): удалены `AGENTS.md`, `CONTRIBUTING.md`, добавлен `work/notes.md`.
- В рабочем дереве (unstaged): правки в devcontainer, сервере, Web UI, типах и сервисах, а также обновленный `tools/server/public/index.html.gz`.
- Неотслеживаемый файл: `tools/server/webui/src/lib/components/app/server/ServerBootstrap.svelte`.

**Добавлено (новое в проекте)**
- Новый API роут в ROUTER-режиме: `POST /models/bootstrap`.
- Новая возможность “bootstrap” модели в ROUTER-режиме через UI (форма запуска модели с аргументами CLI и путём к `.gguf`).
- Новый компонент Web UI: `ServerBootstrap.svelte` (неотслеживаемый файл).
- Новый файл заметок: `work/notes.md` (96 строк, обзор структуры проекта, серверных и UI модулей, RAG и MCP).

**Изменено (по файлам)**
- `.devcontainer/Dockerfile`
- Добавлена установка Node.js 20 из NodeSource (curl + apt-transport-https + nodejs). Это готовит контейнер для сборки/разработки Web UI.

- `.devcontainer/devcontainer.json`
- Добавлен `forwardPorts: [8080]`.
- Добавлен bind-mount `../models` в `/models`.

- `tools/server/server-models.cpp`
- Добавлен парсер строковых CLI-аргументов `split_cli_args()` с поддержкой кавычек и escape; при ошибке — исключение.
- Добавлен метод `server_models::register_model(...)`, который регистрирует модель в runtime, нормализует имя и предотвращает коллизии, добавляя суффиксы `-2`, `-3`, …
- Добавлен обработчик `POST /models/bootstrap`:
- Парсит `args`, `model_path`, `mmproj_path`, `name`, `stop_timeout`.
- Собирает preset из CLI-аргументов, удаляет зарезервированные параметры, может переопределить `model_path`/`mmproj_path`.
- Проверяет наличие источника модели (model или HF repo).
- Автоматически выводит имя модели из пути/репозитория.
- Регистрирует модель и запускает `models.load`.

- `tools/server/server-models.h`
- Объявлен `register_model(...)`.
- Добавлен новый обработчик маршрута `post_router_models_bootstrap`.

- `tools/server/server.cpp`
- Зарегистрирован HTTP маршрут `POST /models/bootstrap`.

- `tools/server/webui/src/lib/services/models.ts`
- Добавлен `ModelsService.bootstrap(...)`, отправляющий `POST /models/bootstrap` и обрабатывающий ошибки.

- `tools/server/webui/src/lib/types/api.d.ts`
- Добавлены типы `ApiRouterModelsBootstrapRequest` и `ApiRouterModelsBootstrapResponse`.

- `tools/server/webui/src/lib/types/index.ts`
- Реэкспортированы новые типы bootstrap.

- `tools/server/webui/src/routes/+page.svelte`
- Добавлена логика определения состояния сервера (loading/error) и режима ROUTER.
- При отсутствии моделей и завершенной загрузке показывается экран `ServerBootstrap`.
- На mount теперь делается `modelsStore.fetch()` с обработкой ошибок.

- `tools/server/webui/src/app.html`
- Корневой контейнер заменен на `<div id="svelte">...</div>` вместо `display: contents`.

- `tools/server/webui/package-lock.json`
- Массовое обновление lockfile: версии множества зависимостей подняты (пример: `@esbuild/*` 0.25.8 → 0.27.2, `@babel/runtime` 7.27.6 → 7.28.6, `@adobe/css-tools` 4.4.3 → 4.4.4 и т.д.).
- `package.json` не изменялся, поэтому это выглядит как результат `npm install`/обновления lockfile.

- `tools/server/public/index.html.gz`
- Обновлен скомпрессированный статический Web UI (размер увеличился примерно на 32 KB). Вероятно соответствует новым UI-изменениям.

**Удалено (staged)**
- `AGENTS.md` (81 строка): инструкции по допустимому использованию AI для контрибьюторов.
- `CONTRIBUTING.md` (185 строк): политика контрибьютинга и AI usage policy, требования к PR и code style.

**Новое поведение/возможности**
- ROUTER-режим сервера получает новый эндпойнт для запуска “ad-hoc” модели с CLI-аргументами и кастомным именем.
- Web UI умеет инициировать запуск модели напрямую из интерфейса при отсутствии моделей.
- Devcontainer готов к UI-разработке (Node.js 20) и пробросу порта 8080, с удобным монтированием `/models`.

