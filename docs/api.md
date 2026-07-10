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
    {"project_id": "fabric-api", "version_id": null},
    {"project_id": "mymod", "source": "local"}
  ],
  "ephemeral": true
}
```

`mods[].source` - `"modrinth"` (по умолчанию) или `"local"`.

- `source: "modrinth"` - `project_id` это slug/id проекта на Modrinth, `version_id` - если не
  задан, берётся последняя версия, совместимая с `mc_version`/`loader`. Required-зависимости
  мода резолвятся и докачиваются автоматически (рекурсивно).
- `source: "local"` - `project_id` это `mod_id` из локально запабленного мода (см.
  `POST /mods/local`), `version_id` - если не задан, берётся самая свежая опубликованная версия
  этого `mod_id`. Зависимости локального мода **не** резолвятся автоматически - если он тянет
  что-то ещё, добавь это отдельным элементом `mods[]`.

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

## `POST /mods/local`

Паблишит незапаблишенный мод локально. `multipart/form-data`, поле `file` - jar мода.

```
curl -F "file=@build/libs/mymod-1.2.0.jar" http://localhost:8000/mods/local
```

`mod_id`, `version`, `loader` (`fabric`/`forge`/`neoforge`) и `mc_version_range` резолвятся из
самого jar'а - `fabric.mod.json` для Fabric, `META-INF/(neoforge.)mods.toml` для Forge/NeoForge.
Если мод с таким же `(mod_id, version)` уже был опубликован - файл и запись **заменяются**
(старый файл удаляется), повторная публикация той же версии - штатный сценарий при итеративной
разработке. Ответ:

```json
{
  "mod_id": "mymod",
  "version": "1.2.0",
  "loader": "fabric",
  "mc_version_range": "~1.20.1",
  "display_name": "My Mod",
  "replaced": false
}
```

422, если файл не zip/jar или в нём нет `fabric.mod.json`/`mods.toml`.

## `GET /mods/local`

Список всех локально опубликованных модов (та самая "отметка" - в отличие от `/cache/mods` эти
никогда не резолвятся через Modrinth, только напрямую по `mod_id`/`version` из `POST /instances`
с `source: "local"`).

## `DELETE /mods/local/{mod_id}/{version}`

Удаляет опубликованную версию мода с диска и из реестра. 404, если такой пары нет.
