import json
import re
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
    SOURCE_DIR = ROOT / "src" / "Coyote"

    if not SOURCE_DIR.exists():
        SOURCE_DIR = ROOT
else:
    SOURCE_DIR = Path(__file__).resolve().parent
    ROOT = SOURCE_DIR.parents[1]


def _resolve_locale_dir():
    """
    New preferred path:
        src/Coyote/language

    Legacy compatibility:
        Coyote/locales
    """
    candidates = [
        SOURCE_DIR / "language",
        ROOT / "locales",
    ]

    for path in candidates:
        if path.exists():
            return path

    # New installations create/use the new directory.
    return SOURCE_DIR / "language"


LOCALE_DIR = _resolve_locale_dir()
DEFAULT_LANGUAGE = "zh_CN"

SUPPORTED_LANGUAGES = (
    "zh_CN",
    "zh_TW",
    "en_US",
    "ja_JP",
)

_locales = {}
_reverse_exact = {}
_current_language = DEFAULT_LANGUAGE


def _safe_read_json(path):
    try:
        value = json.loads(
            Path(path).read_text(
                encoding="utf-8"
            )
        )

        if isinstance(value, dict):
            return value

    except Exception:
        pass

    return {}


def reload_locales():
    global _locales
    global _reverse_exact

    loaded = {}

    for code in SUPPORTED_LANGUAGES:
        path = (
            LOCALE_DIR
            / f"{code}.json"
        )

        data = _safe_read_json(
            path
        )

        if not data:
            data = {
                "meta": {
                    "code": code,
                    "name": code,
                },
                "strings": {},
                "phrases": {},
                "char_map": {},
            }

        loaded[
            code
        ] = data

    _locales = loaded

    reverse = {}

    for code, data in loaded.items():
        for source, translated in (
            data.get(
                "strings",
                {}
            )
            or {}
        ).items():
            if (
                isinstance(source, str)
                and isinstance(
                    translated,
                    str,
                )
                and translated
            ):
                reverse[
                    translated
                ] = source

    _reverse_exact = reverse

    return {
        code: bool(
            loaded.get(
                code
            )
        )
        for code in SUPPORTED_LANGUAGES
    }


def available_languages():
    return list(
        SUPPORTED_LANGUAGES
    )


def language_name(code):
    data = _locales.get(
        code,
        {}
    )

    meta = data.get(
        "meta",
        {}
    )

    return str(
        meta.get(
            "name",
            code,
        )
    )


def get_language():
    return _current_language


def locale_path(
    code=None,
):
    code = (
        code
        or _current_language
    )

    return (
        LOCALE_DIR
        / f"{code}.json"
    )


def set_language(code):
    global _current_language

    code = str(
        code
        or DEFAULT_LANGUAGE
    )

    if code not in SUPPORTED_LANGUAGES:
        code = DEFAULT_LANGUAGE

    _current_language = code
    return code


def _source_from_exact(
    text,
):
    text = str(text)

    return _reverse_exact.get(
        text,
        text,
    )


def _replace_phrases(
    text,
    mapping,
):
    if not mapping:
        return text

    result = str(text)

    # Longest first prevents "输出" from breaking
    # a longer phrase such as "停止全部输出".
    items = sorted(
        (
            (
                str(source),
                str(target),
            )
            for source, target
            in mapping.items()
            if str(source)
        ),
        key=lambda item:
        len(item[0]),
        reverse=True,
    )

    for source, target in items:
        if source in result:
            result = result.replace(
                source,
                target,
            )

    return result


def _replace_chars(
    text,
    mapping,
):
    if not mapping:
        return text

    return "".join(
        str(
            mapping.get(
                char,
                char,
            )
        )
        for char in str(text)
    )


def tr(
    text,
    language=None,
):
    """
    Translate a complete UI string.

    Existing translations are first normalized back to their
    canonical source string, which makes live language switching
    possible without recreating the whole window.
    """
    if text is None:
        return ""

    language = (
        language
        or _current_language
    )

    source = _source_from_exact(
        str(text)
    )

    data = _locales.get(
        language,
        {}
    )

    strings = (
        data.get(
            "strings",
            {}
        )
        or {}
    )

    if source in strings:
        return str(
            strings[source]
        )

    return tr_dynamic(
        source,
        language,
    )


def tr_dynamic(
    text,
    language=None,
):
    """
    Translate logs and runtime text by combining:
    1. exact-string lookup
    2. phrase replacement
    3. optional character conversion (mainly zh_TW)
    """
    if text is None:
        return ""

    language = (
        language
        or _current_language
    )

    source = _source_from_exact(
        str(text)
    )

    data = _locales.get(
        language,
        {}
    )

    strings = (
        data.get(
            "strings",
            {}
        )
        or {}
    )

    if source in strings:
        return str(
            strings[source]
        )

    result = _replace_phrases(
        source,
        data.get(
            "phrases",
            {},
        )
        or {},
    )

    result = _replace_chars(
        result,
        data.get(
            "char_map",
            {},
        )
        or {},
    )

    return result


def tr_format(
    source,
    language=None,
    **values,
):
    translated = tr(
        source,
        language,
    )

    try:
        return translated.format(
            **values
        )
    except Exception:
        return translated


def localize_log_record(
    item,
    language=None,
):
    """
    Return a translated copy of one event-log row.
    The stored on-disk JSONL remains raw/original.
    """
    language = (
        language
        or _current_language
    )

    return {
        "time": str(
            item.get(
                "time",
                "",
            )
        ),
        "category": tr_dynamic(
            item.get(
                "category",
                "",
            ),
            language,
        ),
        "event": tr_dynamic(
            item.get(
                "event",
                "",
            ),
            language,
        ),
        "detail": tr_dynamic(
            item.get(
                "detail",
                "",
            ),
            language,
        ),
        "output": item.get(
            "output",
            {},
        ),
    }


reload_locales()