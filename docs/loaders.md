# Как работают загрузчики

Все три загрузчика в итоге дают **launch-профиль** - json с `mainClass`, `libraries` и
`arguments` (game/jvm), который мерджится с vanilla version json через `inheritsFrom`
(`app/services/loaders/common.py:merge_with_vanilla`). Дальше общий для всех `launch.py`
подставляет `${auth_player_name}`, `${classpath}` и т.п. и собирает финальную java-команду.
На этом сходство заканчивается - Fabric и Forge/NeoForge устроены принципиально по-разному
внутри, и большая часть боли была именно в этом.

## Fabric - простой случай

`meta.fabricmc.net` отдаёt готовый launch-профиль (`GET /v2/versions/loader/{mc}/{loader}/profile/json`)
без всякого установщика. Профиль ссылается на реальные maven-координаты (`fabric-loader`,
`intermediary`, `asm`, `sponge-mixin`) с нормальными URL - их просто качаем в общий
`cache/libraries/` и кладём все на classpath (`-cp`), как и обычные vanilla-библиотеки.
Никакого `library_directory` не нужно. Это `include_child_libraries=True` в `merge_with_vanilla`.

## Forge / NeoForge - installer.jar

Тут нет простого json - есть `forge-<mc>-<version>-installer.jar` (для NeoForge -
`neoforge-<version>-installer.jar`, тот же формат, просто другой maven-репозиторий:
`maven.neoforged.net`). Установщик умеет headless-режим: `java -jar installer.jar --installClient <dir>`.

Четыре нюанса, из-за которых пришлось повозиться:

1. **Установщику нужен фейковый `.minecraft`.** Без `launcher_profiles.json` в целевой
   директории он падает с `There is no minecraft launcher profile in ...`. Кладём туда
   минимальный стаб перед запуском (`common.py:_LAUNCHER_PROFILES_STUB`).
2. **Установщик пишет свой `versions/<id>/<id>.json` до того, как реально допатчит клиентские
   jar'ы.** Если сетевая загрузка внутри установщика (например, mojmaps) обрывается, json
   уже есть на диске, но патченые jar'ы - нет. Проверять "уже установлено" по наличию json
   недостаточно - используем отдельный маркер `.modrig_install_complete`, который пишем
   только после `exit code == 0` (`common.py:run_installer`/`ensure_installed`).
3. **Ванильная директория в `versions/` не всегда называется как запрошенный `mc_version`.**
   `find_produced_version` изначально исключала папку по точному совпадению имени с
   `mc_version` и брала первую оставшуюся - работало, пока не встретилось MC 26.x: запрос
   `mc_version="26.1"` (это тоже отдельная валидная запись в манифесте Mojang, не просто алиас)
   ставит установщик против конкретного патча `"26.1.2"`, и именно так называется его
   собственная vanilla-папка - не совпадает с "26.1", не исключается, и как более ранняя по
   алфавиту (`26.1.2` < `neoforge-...`) выбирается ПЕРВОЙ, то есть в качестве "профиля загрузчика"
   по ошибке берётся сам vanilla json. Из-за этого mainClass в команде запуска оказывался
   ванильным (`net.minecraft.client.main.Main`), а game-аргументы дублировались (vanilla
   смерджился сам с собой). Фикс - искать директорию с ключом `inheritsFrom` в её json (у
   загрузочного профиля он есть всегда, у обычного vanilla version.json - никогда), и только
   если такой не нашлось - откатываться на старое сравнение по имени.

   Заодно стало видно, что у NeoForge для 26.x другой mainClass - `net.neoforged.fml.startup.Client`,
   а не `cpw.mods.bootstraplauncher.BootstrapLauncher` из более старых версий. **Это не линейная
   эволюция "старое → новое"** - Forge и NeoForge разошлись форком на 1.20.1 и с тех пор меняют
   bootstrap независимо друг от друга и не синхронно по времени: Forge переключился на свой
   `net.minecraftforge.bootstrap.ForgeBootstrap` уже на 1.20.6 (50.x) - раньше, чем NeoForge вообще
   отошёл от `BootstrapLauncher` (тот у NeoForge держался как минимум до 1.21.x). Так что "новее"
   тут не значит "более поздная версия Minecraft" - у каждого проекта свой график. Пока для
   `net.neoforged.fml.startup.Client` отдельная ветка не понадобилась (см. следующий пункт про
   `ForgeBootstrap` - если у него окажутся те же требования, возможно понадобится и здесь).
4. **Часть classpath вообще не описана в json.** У Forge/NeoForge реальный игровой jar
   (`client-*-srg.jar`, `client-*-extra.jar`, `forge-*-client.jar`/`neoforge-*-client.jar`) -
   результат локального бинарного патчинга внутри установщика, у него просто нет download URL.
   Профиль объявляет только вспомогательные библиотеки (`bootstraplauncher`, `securejarhandler`,
   `eventbus`, `fmlloader`, `mixin` и т.д.) - их кладём на classpath как обычно
   (`include_child_libraries=True`, как и у Fabric). А патченые jar'ы находит **сам**
   `cpw.mods.bootstraplauncher.BootstrapLauncher` при старте, сканируя директорию, на которую
   указывает `-DlibraryDirectory=${library_directory}` - это путь к `libraries/` конкретно
   внутри `forge_installs/<mc>-<version>/` (не в общий shared-кэш!), потому что именно там
   установщик их создал.

   Мы **сознательно не** кладём вообще все файлы из этой директории на classpath (было
   соблазнительно - искали же именно так, "чтобы наверняка"), потому что там же лежат jar'ы
   инструментов самого установщика (`ForgeAutoRenamingTool` и т.п.), которые тащат в себе
   copy ASM - и это ломает JPMS split-package resolution (`Modules X and Y export package Z`).

`library_directory` и `classpath_separator` - два плейсхолдера, которых нет в vanilla json,
но которые использует `-p` (module path) аргумент в forge/neoforge json; `launch.py` их
подставляет всегда, для Fabric они просто не встречаются в шаблоне и ни на что не влияют.

### Дубли в classpath

Vanilla и Forge/NeoForge иногда объявляют одну и ту же библиотеку немного по-разному
(например Forge пишет `com.google.guava:failureaccess:1.0.1@jar` - с суффиксом `@jar`,
которого нет в vanilla-варианте того же артефакта). Дедуп по имени координаты это не ловит,
но обе записи резолвятся в один и тот же файл на диске - и в результате один и тот же путь
дважды попадал в `-cp`. `BootstrapLauncher` на это падает с `IllegalStateException: Duplicate key`
(он сам строит `UnionFileSystem` из classpath-записей). Поэтому classpath дедуплицируется по
итоговому пути файла, а не по имени координаты (`instance_pool.py`, `dict.fromkeys(classpath_jars)`).

### Дубли между classpath и module path

Отдельная история от предыдущей: библиотеки из литерального `-p` аргумента (bootstraplauncher,
securejarhandler, `asm*`, `JarJarFileSystems`) объявлены загрузчиком **и** в `-p`, **и** в
обычном списке `libraries` профиля. Раз они уже в `include_child_libraries=True`, они попадали
и на `-cp` тоже - какое-то время это молча проглатывалось, но более новый `securejarhandler`
(встречается уже на некоторых NeoForge-сборках) начал строго падать с
`IllegalStateException: Module named X was already on the JVMs module path` на такой двойной
регистрации одного и того же модуля.

Первая версия фикса сравнивала конкретные basename'ы jar'ов (`asm-9.8.jar` и т.п.) - помогло не
до конца: на 1.21.6 всплыл вариант, где **vanilla** version json сам объявляет
`org.ow2.asm:asm:9.6` (Mojang использует ASM для чего-то внутреннего), а NeoForge независимо
несёт на `-p` `asm:9.8`. Разные файлы, разные версии - но JVM ругается на **имя модуля**
(`org.objectweb.asm`), а не на версию или путь, так что сравнение по basename'у их не ловит.

Фикс - `merge_with_vanilla` парсит литеральное значение `-p` из **своих же** (ещё не
подставленных) jvm-аргументов загрузчика (`_module_path_artifact_keys`: ищет `-p`, разбивает
следующую за ним строку по `${classpath_separator}`, из каждого пути
`${library_directory}/<group-path>/<artifactId>/<version>/<file>.jar` берёт только
`<group-path>/<artifactId>`, отбрасывая версию и имя файла). Дальше **и** vanilla-, **и**
загрузчик-декларированные библиотеки с таким же `group/artifact` (`_artifact_key`) не
добавляются на `-cp`, независимо от их версии - они всё равно доступны игре через `-p`.

### Второй, несовместимый bootstrap-механизм: ForgeBootstrap

Начиная с Forge 1.20.6 (50.x) mainClass - `net.minecraftforge.bootstrap.ForgeBootstrap`, не
`cpw.mods.bootstraplauncher.BootstrapLauncher`. У него нет `-p`/`-DlibraryDirectory` вообще (jvm
args - буквально один `-Djava.net.preferIPv6Addresses=system`). Раскопали декомпиляцией
`bootstrap-2.1.8.jar` (`net.minecraftforge.bootstrap.Bootstrap.findAllClassPathEntries()`): он
находит свои модули, парся `System.getProperty("java.class.path")` - то есть обычный `-cp`, без
какого-либо самостоятельного сканирования директорий.

Из-за этого механизм с `library_directory`-сканированием (см. предыдущий пункт) для него просто
не работает - локально пропатченный `forge-*-client.jar` (объявлен в `libraries` с пустым
`artifact.url`, т.к. его негде взять по сети) должен быть явно на `-cp`, а не только физически
лежать в директории, которую никто не сканирует. Без этого падает `IllegalStateException: Could
not find net/minecraft/client/Minecraft.class in classloader SecureModuleClassLoader[...]` -
т.е. запускается, но не находит пропатченный игровой код вообще.

Заодно всплыл смежный баг: `resolve_library_artifact` (`mojang.py`) не проверял, что
`artifact.url` вообще непустой, и пытался скачать по `url=""` - `httpx.UnsupportedProtocol:
Request URL is missing an 'http://' or 'https://' protocol`. Теперь такие записи считаются
"недоступны для скачивания" (возвращается `None`, как и раньше для `rules`-запрета).

Фикс - `common.py:uses_module_path()` проверяет, есть ли в **своих же** jvm-аргументах загрузчика
буквальный `-p` (module path). Если нет (`ForgeBootstrap` и, возможно, будущие подобные
механизмы) - `local_only_library_paths()` находит все объявленные библиотеки с пустым
`artifact.url`, резолвит их физическое расположение прямо в `install_dir/libraries/<path>` (туда
их положил установщик) и кладёт на `-cp` через `LoaderResult.extra_classpath_jars`. Если `-p`
присутствует (`BootstrapLauncher`) - ничего дополнительно не добавляем, как и раньше, чтобы не
вернуть дубли модулей из предыдущего пункта.

## Версии загрузчика по умолчанию

- **Fabric** - берётся первая `stable: true` запись из `meta.fabricmc.net`.
- **Forge** - `<mc>-recommended`, иначе `<mc>-latest` из `promotions_slim.json`.
- **NeoForge** - версии там не привязаны к MC явно, только числовым префиксом:
  MC `1.20.4` → NeoForge `20.4.x`, MC `1.21` → `21.0.x` (patch по умолчанию `0`). Из всех
  версий с нужным префиксом берётся самая новая **не**-beta, если такая есть.

## Известные ограничения

- Очень старые версии Forge исторически были GUI-only без надёжного headless-режима - если
  `--installClient` не поддерживается, `run_installer` просто упадёт с текстом вывода
  установщика в ошибке; отдельного определения диапазона версий пока нет.
- MS OAuth не реализован - только offline-авторизация (см. `architecture.md`).
