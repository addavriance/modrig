# v0.0.10
- Поддержан второй, несовместимый bootstrap-механизм Forge (`net.minecraftforge.bootstrap.ForgeBootstrap`,
  с 1.20.6/50.x) - падал с `Could not find net/minecraft/client/Minecraft.class`, т.к. этот
  механизм не сканирует `-DlibraryDirectory` и требует локально пропатченный client-jar явно на
  classpath (см. [loaders](loaders.md#второй-несовместимый-bootstrap-механизм-forgebootstrap))
- Заодно пофикшен смежный краш `httpx.UnsupportedProtocol` при попытке скачать библиотеку с
  пустым `artifact.url` (такие теперь просто не резолвятся для скачивания)

# v0.0.9
- Пофикшен неверный выбор launch-профиля для NeoForge на MC 26.x: запрос версии-алиаса (`26.1`,
  резолвится установщиком в конкретный патч `26.1.2`) приводил к тому, что вместо профиля
  загрузчика бралась vanilla-версия (mainClass ванильный, game-аргументы задвоены), см.
  [loaders](loaders.md#forge--neoforge---installerjar)

# v0.0.8
- Автозагрузка управляемого JRE от Mojang (то же самое, что делает официальный лаунчер) - для
  версии/лоадера, которым нужна Java поновее, `JAVA_HOME_<N>` больше не обязателен, modrig сам
  тянет нужный `java-runtime-*` в `cache/runtimes/` (см. [architecture](architecture.md#выбор-jdk))

# v0.0.7
- Добавлена preflight-проверка версии Java перед запуском: если под нужный `javaVersion.majorVersion`
  не настроен `JAVA_HOME_<N>` и дефолтная `java` слишком старая, инстанс падает сразу с понятной
  ошибкой (`status: failed`) вместо непонятного краша JVM в середине старта (см. [architecture](architecture.md#выбор-jdk))

# v0.0.6
- Расширен фикс module-path дублей из v0.0.5: теперь ловит и разные версии одного и того же
  артефакта (vanilla asm-9.6 vs NeoForge asm-9.8) через сравнение по `group/artifact`, а не по
  точному имени jar-файла

# v0.0.5
- Пофикшена `IllegalStateException: Module named X was already on the JVMs module path` на
  некоторых NeoForge-сборках (библиотека попадала и на classpath, и на module path одновременно,
  см. [loaders](loaders.md#дубли-между-classpath-и-module-path))

# v0.0.4
- Резолв нужного JDK по `javaVersion.majorVersion` из version json (`JAVA_HOME_<N>` в env), вместо
  всегда дефолтного `java` из PATH
- Пофикшена гонка при параллельной загрузке двух библиотек в один и тот же файл (падало с
  `WinError 32` на Windows)

# v0.0.3
- Доблавена возможность паблишить моды локально (см. [api](api.md))

# v0.0.2
- Добавлена поддержка forge/neoforge

# v0.0.1
- Добавлена поддержка fabric
- Построено базовое приложение для создания игровых инстансов