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