# REST API

Базовый URL - `http://localhost:8000` (см. `main.py`). Схемы запросов/ответов: `app/models.py`.

## `POST /instances`

Создаёт инстанс и сразу запускает подготовку в фоне (ответ приходит со статусом `preparing`,
не дожидаясь скачивания).

```json
{
  "mc_version": "1.20.1",
  "loader": "fabric",          // "fabric" | "forge" | "neoforge"
  "loader_version": null,       // необязательно - если не задано, берётся latest/recommended
  "mods": [
    {"project_id": "fabric-api", "version_id": null}
  ],
  "ephemeral": true
}
```

`mods[].project_id` - slug или id проекта на Modrinth. `version_id` - если не задан, берётся
последняя версия, совместимая с `mc_version`/`loader`. Required-зависимости мода резолвятся и
докачиваются автоматически (рекурсивно).

Ответ - `InstanceInfo` (см. ниже), `id` нужен для всех остальных запросов.

## `GET /instances`

Список инстансов, которые ещё есть в пуле (активные + недавние - эфемерные пропадают из пула
только по явному внутреннему циклу, не сразу после завершения). Не путать с историей -
`/instances/{id}/logs` работает даже для тех, кого тут уже нет.

## `GET /instances/{id}`

```json
{
  "id": "...",
  "mc_version": "1.20.1",
  "loader": "fabric",
  "loader_version": "0.19.3",       // резолвится и подставляется автоматически
  "ephemeral": true,
  "status": "running",              // preparing | downloading | running | stopped | exited | crashed | failed
  "created_at": "2026-07-09T...",
  "finished_at": null,
  "exit_code": null,
  "error": null                     // заполнено только при status=failed (ошибка на этапе подготовки)
}
```

`failed` - не тоже самое, что `crashed`: `failed` значит, что процесс так и не запустился
(ошибка резолва/скачивания/установки загрузчика), `crashed` - процесс стартовал и упал.

## `POST /instances/{id}/stop`

Просит инстанс остановиться (`terminate()` процесса). Финальный статус в любом случае будет
`stopped`, даже если JVM не завершилась кодом 0 - сам факт "мы попросили" важнее exit code
(Java почти никогда не выходит с 0 после `terminate`/SIGTERM, т.к. закрывать по-хорошему
нечем - окна нет). 409, если инстанс не в статусе `running`.

## `DELETE /instances/{id}`

Ручная чистка рабочей директории (`instances/<id>/`) для **не**эфемерных инстансов. 409, пока
инстанс `preparing`/`downloading`/`running` - сначала `stop`.

## `GET /instances/{id}/logs?from_line=0&limit=200`

Пагинация по `history/<id>/run.log`. Работает и после `DELETE`/эфемерной очистки - лог живёт
отдельно от рабочей директории инстанса.

## `GET /instances/{id}/crash`

```json
{
  "status": "crashed",
  "exit_code": 1,
  "crash_report": "=== crash-2026-07-09.txt ===\n...",  // null, если крэш-репортов не было
  "log_tail": ["...", "..."]                              // последние ~200 строк лога
}
```

Крэш-репорты (`crash-reports/*.txt`) и JVM native crashes (`hs_err_pid*.log`) собираются из
рабочей директории инстанса **до** её удаления и сохраняются в `history/<id>/crash.txt`.

## `GET /cache/versions`

Записи реестра кэша с kind `version` (client.jar), `loader_profile` (Fabric/Forge/NeoForge
профили) и `asset_index`.

## `GET /cache/mods`

Скачанные файлы модов (kind `mod`), ключ - sha1.
