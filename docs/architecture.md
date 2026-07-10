# Архитектура

Сервис скачивает всё нужное для запуска Minecraft-клиента (версию, загрузчик, моды), поднимает
его в изолированной директории-инстансе и отслеживает лог/креш через REST API.

## Слои

```
main.py                        # FastAPI app, lifespan (init_db/close_db)
app/api/                       # HTTP-роуты (instances.py, cache.py)
app/services/instance_pool.py  # оркестрация: prepare -> download -> launch -> supervise -> cleanup
app/services/loaders/          # Fabric/Forge/NeoForge - см. loaders.md
app/services/mojang.py         # piston-meta: version manifest, client.jar, библиотеки, ассеты, natives
app/services/modrinth.py       # резолвер модов + рекурсивные required-зависимости
app/services/local_mods.py     # локально запабленные моды (POST /mods/local) - парсинг jar'а, замена по (mod_id, version)
app/services/launch.py         # сборка java-командной строки (подстановка ${...}, OS-правила)
app/services/auth.py           # offline-авторизация (оффлайн UUID, как у ванильного клиента)
app/services/cache.py          # реестр shared-кэша поверх SQLite (для /cache/*)
app/services/history.py        # логи/крэши вне instance-директории
app/db.py                      # SQLite: cache_entries, runs, local_mods
app/config.py                  # Settings (пути, урлы, таймауты, java_homes из JAVA_HOME_<N>)
```

## Жизненный цикл инстанса

`POST /instances` создаёт `RuntimeInstance` в памяти пула и сразу пишет запись в SQLite
(`runs`), затем асинхронно:

1. **preparing → downloading** - резолвится версия загрузчика, тянется vanilla version json,
   загрузчик готовит свой профиль (см. `loaders.md`), качаются client.jar/библиотеки/ассеты/natives,
   резолвятся и качаются моды - с Modrinth (`source: "modrinth"`) и/или из локальной БД
   (`source: "local"`, см. `POST /mods/local`).
2. **running** - под семафором `max_concurrent_instances` собирается java-команда и стартует
   процесс; stdout/stderr пишутся построчно в `history/<id>/run.log` (не в instance-директорию -
   это важно для эфемерных инстансов, см. ниже).
3. Финал - **stopped** (мы сами попросили остановиться), **exited** (exit code 0 без нашего
   запроса) или **crashed** (что угодно другое). Если есть `crash-reports/*.txt` или
   `hs_err_pid*.log` - их содержимое сохраняется в `history/<id>/crash.txt`.
4. Если инстанс `ephemeral` - его рабочая директория (`instances/<id>/`: копии модов, saves,
   natives) удаляется. `history/<id>/` и общий кэш (`cache/`) не трогаются никогда.

## Shared cache

`data/cache/` - общий для всех инстансов, дедуп по content-hash/координатам:

- `versions/<mc_version>/client.jar`
- `libraries/<maven-путь>` - обычные библиотеки (LWJGL, netty, guava и т.д.)
- `assets/objects/<hash[:2]>/<hash>` - ассеты по content-hash, как в реальном `.minecraft/assets`
- `natives/<mc_version>/` - распакованные natives (нужно только для до-1.19 версий; современный
  LWJGL умеет сам себя распаковывать из classpath-jar'ов)
- `mods/<sha1[:2]>/<sha1>-<filename>.jar` - файлы модов с Modrinth
- `loader_profiles/`, `forge_installs/<mc>-<version>/`, `neoforge_installs/<mc>-<version>/` -
  см. `loaders.md`

`GET /cache/versions` и `GET /cache/mods` читают реестр этих записей из SQLite (таблица
`cache_entries`), а не сканируют диск.

## Локальные моды

`data/local_mods/<mod_id>/<version>/<filename>.jar` - **не** часть shared-кэша: это не то, что
можно перекачать заново, а то, что пользователь сам туда положил через `POST /mods/local`
(незапаблишенные/WIP моды - основной сценарий использования сервиса). Ключ - `(mod_id, version)`
из собственного манифеста мода (`fabric.mod.json` / `META-INF/(neoforge.)mods.toml`), не
content-hash: повторная публикация той же пары **заменяет** файл и запись в таблице
`local_mods`. `ModRef.source: "local"` в `POST /instances` резолвит мод напрямую отсюда, минуя
Modrinth целиком.

## Выбор JDK

Каждый vanilla version json несёт `javaVersion.majorVersion` (например `21` для 1.21.1, `17`
для 1.20.1) - тянем его вместе с остальным version json и резолвим java-бинарник под конкретную
версию через `Settings.resolve_java_bin` (`app/config.py`): смотрим `java_homes[majorVersion]`
(заполняется из переменных окружения `JAVA_HOME_<N>`, например `JAVA_HOME_21=C:\...\jdk-21`) и
если там есть `bin/java(.exe)` - используем его; иначе просто `"java"` из `PATH`, как раньше.
Так один и тот же сервис может гонять и старые версии (Java 8/17), и новые (Java 21+), если
дефолтная `java` в `PATH` не подходит всем сразу. Резолвнутое значение попадает в
`launch.build_command(java_bin=...)` вместо дефолтного `"java"`; сам major version заодно
сохраняется в реестре кэша (`GET /cache/versions` → `java_major_version`) для справки.

## Конкурентность

`InstancePool` держит `asyncio.Semaphore(max_concurrent_instances)` - но только вокруг самого
запуска JVM (`_launch`), а не вокруг подготовки (resolve/download). Т.е. можно параллельно качать
ассеты для N инстансов, но одновременно **работающих** JVM будет не больше лимита.

## Авторизация

Только offline-режим (см. открытый вопрос в исходном плане проекта - MS OAuth пока не решён).
`app/services/auth.py` генерирует детерминированный UUID (тот же алгоритм, что и у ванильного
клиента для offline-игроков: `MD5("OfflinePlayer:<name>")` с выставленными version/variant
битами) и случайный access token - этого достаточно для одиночной игры и большинства модов.
