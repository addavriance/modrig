from __future__ import annotations

from pathlib import Path

from app.services.auth import AuthProfile
from app.services.mojang import CURRENT_OS, rule_permits

CLASSPATH_SEP = ";" if CURRENT_OS == "windows" else ":"

_DEFAULT_JVM_ARGS = [
    "-Djava.library.path=${natives_directory}",
    "-cp",
    "${classpath}",
]


def _substitute(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace("${%s}" % key, value)
    return template


def _build_args(arg_list: list, values: dict[str, str]) -> list[str]:
    result: list[str] = []
    for item in arg_list:
        if isinstance(item, str):
            result.append(_substitute(item, values))
        elif isinstance(item, dict):
            if not rule_permits(item.get("rules")):
                continue
            value = item["value"]
            if isinstance(value, list):
                result.extend(_substitute(v, values) for v in value)
            else:
                result.append(_substitute(value, values))
    return result


def build_command(
    profile: dict,
    mc_version: str,
    instance_dir: Path,
    natives_dir: Path,
    assets_dir: Path,
    classpath_jars: list[Path],
    client_jar: Path,
    auth: AuthProfile,
    java_bin: str = "java",
    library_directory: Path | None = None,
    include_client_jar: bool = True,
) -> list[str]:
    classpath_entries = [*classpath_jars, client_jar] if include_client_jar else list(classpath_jars)
    classpath = CLASSPATH_SEP.join(str(p) for p in classpath_entries)

    values = {
        "auth_player_name": auth.username,
        "version_name": profile.get("id", mc_version),
        "game_directory": str(instance_dir),
        "assets_root": str(assets_dir),
        "assets_index_name": profile["assetIndex"]["id"],
        "auth_uuid": auth.uuid,
        "auth_access_token": auth.access_token,
        "clientid": "modrig",
        "auth_xuid": "0",
        "user_type": auth.user_type,
        "version_type": profile.get("type", "release"),
        "natives_directory": str(natives_dir),
        "launcher_name": "modrig",
        "launcher_version": "0.1",
        "classpath": classpath,
        "classpath_separator": CLASSPATH_SEP,
        "library_directory": str(library_directory) if library_directory else "",
    }

    arguments = profile.get("arguments")
    if arguments is not None:
        jvm_args = _build_args(arguments.get("jvm", []), values) or [_substitute(a, values) for a in _DEFAULT_JVM_ARGS]
        game_args = _build_args(arguments.get("game", []), values)
    else:
        # Legacy (pre-1.13) version jsons use a flat "minecraftArguments" string instead.
        jvm_args = [_substitute(a, values) for a in _DEFAULT_JVM_ARGS]
        game_args = [_substitute(tok, values) for tok in profile["minecraftArguments"].split()]

    return [java_bin, *jvm_args, profile["mainClass"], *game_args]
