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
app/services/jre.py            # автозагрузка управляемого JRE от Mojang - см. "Выбор JDK" ниже
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

Каждый vanilla version json несёт `javaVersion` (например `{"component": "java-runtime-epsilon",
"majorVersion": 25}` для 26.x, `{"component": "java-runtime-delta", "majorVersion": 21}` для
1.21.1) - тянем его вместе с остальным version json и резолвим java-бинарник под конкретную
версию по цепочке приоритетов (`instance_pool.py`):

1. **`JAVA_HOME_<majorVersion>`**, если настроена (`Settings.java_home_override`,
   `app/config.py`) - явный оверрайд для тех, кто хочет свой конкретный JDK.
2. **Управляемый JRE от Mojang** (`app/services/jre.py:ensure_runtime`) - то же самое, что делает
   официальный лаунчер: тянет фиксированный индекс
   `piston-meta.mojang.com/.../java-runtime/.../all.json`, находит нужный `component`
   (`java-runtime-alpha/beta/gamma/delta/epsilon`, `jre-legacy`) для текущей ОС, качает полный
   список файлов рантайма (сотни файлов, каждый с sha1, тот же механизм, что и ассеты) в общий
   кэш `cache/runtimes/<component>/` и возвращает путь к `bin/java(.exe)`. Пользователю вообще
   не нужно самому ставить и настраивать JDK под каждую встречающуюся версию.
3. **Голый `"java"` из `PATH`** - если по какой-то причине managed-рантайм не скачался
   (например Mojang не публикует нужный `component` под текущую ОС).

Резолвнутое значение попадает в `launch.build_command(java_bin=...)` вместо дефолтного
`"java"`; major version заодно сохраняется в реестре кэша (`GET /cache/versions` →
`java_major_version`), а сам managed-рантайм регистрируется отдельной записью с `kind=runtime`.

Отдельно, перед запуском сервис разово гоняет `<резолвнутый java_bin> -version`, парсит
major-версию из вывода и, если она меньше требуемой (managed-рантайм не скачался и голый `java`
из PATH оказался слишком старым), падает сразу с понятной ошибкой (`status: failed`, а не
`crashed`) вида `Minecraft 26.2 needs Java 25+, but 'java' resolves to Java 17. Set
JAVA_HOME_25 to a Java 25+ install and restart modrig.` - вместо того, чтобы стартовать
заведомо обречённый процесс и потом разбирать малопонятный крэш JVM (например `Unrecognized
option: --sun-misc-unsafe-memory-access=allow` - флаг, которого не знают JDK старше ~23).

## Конкурентность

`InstancePool` держит `asyncio.Semaphore(max_concurrent_instances)` - но только вокруг самого
запуска JVM (`_launch`), а не вокруг подготовки (resolve/download). Т.е. можно параллельно качать
ассеты для N инстансов, но одновременно **работающих** JVM будет не больше лимита.

## Авторизация

Только offline-режим (см. открытый вопрос в исходном плане проекта - MS OAuth пока не решён).
`app/services/auth.py` генерирует детерминированный UUID (тот же алгоритм, что и у ванильного
клиента для offline-игроков: `MD5("OfflinePlayer:<name>")` с выставленными version/variant
битами) и случайный access token - этого достаточно для одиночной игры и большинства модов.
