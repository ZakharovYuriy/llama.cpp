# Заметки по проекту (llama.cpp)

## Структура проекта (краткая карта)
- Ядро C/C++: `src/`, `include/`, `common/`
- Сборка и конфигурация: `CMakeLists.txt`, `CMakePresets.json`, `cmake/`, `Makefile`
- Сервер (HTTP + API + маршруты): `tools/server/`
  - Реализация сервера: `tools/server/server.cpp`, `tools/server/server-http.cpp`, `tools/server/server-context.cpp`
  - Документация сервера: `tools/server/README.md`, `tools/server/README-dev.md`
- Web UI (SvelteKit): `tools/server/webui/`
- Статические UI-ассеты: `tools/server/public*`
- Мультимодальные модели: `docs/multimodal.md`
- Function calling / tool use: `docs/function-calling.md`

## Ответы на вопросы (web, RAG, файлы, инструкции, MCP)

### Web UI: что это, на чем основано, как открывается, что бэкенд
- Бэкенд — `llama-server` (C++), использует `cpp-httplib` и `nlohmann::json`.
  - См. `tools/server/README.md`, `tools/server/server-http.cpp`.
- Web UI — SvelteKit‑приложение (Vite, TailwindCSS, shadcn-svelte, IndexedDB/Dexie).
  - См. `tools/server/README-dev.md` и `tools/server/webui/`.
- Страница открывается по корню сервера, напр. `http://127.0.0.1:8080/`.
  - Сервер отдает либо встроенный `index.html.gz` (вшит в `llama-server`), либо статику через `--path`.
  - См. `tools/server/server-http.cpp` и `tools/server/README.md`.

### RAG (Retrieval-Augmented Generation)
- Встроенного RAG‑пайплайна в сервере не обнаружено.
- Есть эндпойнты эмбеддингов и реранка, их можно использовать для внешнего RAG.
  - См. `tools/server/README.md` и маршруты в `tools/server/server.cpp`.

### Как Web UI добавляет файлы в контекст модели
- Web UI обрабатывает файлы в браузере:
  - Текстовые файлы: чтение как UTF-8.
  - Изображения/аудио: конвертация в base64 data URL.
  - PDF: конвертация в текст или в изображения (если используется vision‑модель).
  - См. `tools/server/webui/src/lib/utils/process-uploaded-files.ts`.
- Перед отправкой в API вложения превращаются в content parts:
  - `text` для текстовых файлов и PDF‑контента
  - `image_url` для изображений (base64)
  - `input_audio` для аудио
  - См. `tools/server/webui/src/lib/services/chat.ts`.
- Запросы идут в `/v1/chat/completions` (OpenAI‑совместимый).
  - См. `tools/server/server.cpp` и `tools/server/README.md`.

### Можно ли использовать .md (instructions.md) как кастомные инструкции?
- `.md` распознается как текстовый файл и поддерживается для загрузки в Web UI.
  - См. `tools/server/webui/src/lib/enums/files.ts` и `tools/server/webui/src/lib/utils/text-files.ts`.
- При прикреплении содержимое добавляется как `text`‑часть промпта (как у любого текстового файла).
  - См. `tools/server/webui/src/lib/services/chat.ts`.
- Специального автоподхвата `instructions.md` в коде нет.
  - Используйте системные сообщения или шаблоны промптов (см. `tools/server/README.md`).

### MCP (Model Context Protocol)
- Интеграции MCP в репозитории не найдено.
- Если требуется, это нужно добавлять извне (например, прокси‑слой перед OpenAI‑совместимым API).

## Разбор веб-сервера, UI и контекста (детально)

### Запуск моделей
- MODEL режим: `tools/server/server-context.cpp` — `load_model()` грузит GGUF, инициализирует `llama_context`, слоты, batch, mmproj.
- ROUTER режим: `tools/server/server-models.cpp` — спавнит дочерние `llama-server` процессы, управляет LRU и статусами.
- Chat template из GGUF: `src/llama-model.cpp` — `llama_model_chat_template()`.
- Инициализация контекста: `common/common.cpp` (вызывается из `tools/server/server-context.cpp`).

### HTTP и обработка запросов
- HTTP слой + middleware: `tools/server/server-http.cpp`.
- Роуты и ответы: `tools/server/server-context.cpp` (`server_routes`, `server_res_generator`).
- Очереди задач/ответов: `tools/server/server-queue.h`.
- Типы задач и параметры: `tools/server/server-task.h`.
- Разбор JSON, мультимодал, chat-template: `tools/server/server-common.cpp`.

### Разметка / chat templates
- Применение шаблонов: `common/chat.cpp`, `common/chat.h`.
- Переопределяемые Jinja-шаблоны: `models/templates/README.md`, `models/templates/*.jinja`.
- Экспорт шаблона в `/props`: `tools/server/server-context.cpp`.
- `/apply-template`: `tools/server/server-common.cpp`, `tools/server/server-context.cpp`.

### Сессии и контекст на сервере
- Сессии = слоты (sequence) внутри единственного server-потока: `tools/server/server-context.cpp`.
- Выбор слота по LCP + prompt cache: `tools/server/server-context.cpp`.
- Сохранение/восстановление/очистка слотов: `tools/server/server-context.cpp` (`SERVER_TASK_TYPE_SLOT_*`).
- Отложенные задачи до освобождения слота: `tools/server/server-queue.h`.

### Web UI: чаты и сессии
- Конверсации, ветвление, навигация: `tools/server/webui/src/lib/stores/conversations.svelte.ts`.
- Стрим и управление активным ответом: `tools/server/webui/src/lib/stores/chat.svelte.ts`.
- Хранилище истории (IndexedDB): `tools/server/webui/src/lib/services/database.ts`.
- API чат-комплишнов: `tools/server/webui/src/lib/services/chat.ts`.
- Модели и режим сервера: `tools/server/webui/src/lib/stores/models.svelte.ts`, `tools/server/webui/src/lib/stores/server.svelte.ts`.

### Используемые библиотеки
- `vendor/cpp-httplib/httplib.h` — HTTP сервер.
- `vendor/nlohmann/json.hpp` — JSON.
- `vendor/sheredom/subprocess.h` — запуск подпроцессов (ROUTER).
- `common/jinja/*` — Jinja-шаблоны.
- `common/peg-parser.*`, `common/chat-parser.*` — парсинг structured output / tool calls.
- `tools/server/webui/package.json` — стек WebUI (SvelteKit, Tailwind, Dexie и др.).
