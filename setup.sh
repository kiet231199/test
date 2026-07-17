#!/usr/bin/env bash

_media_check_is_sourced() {
    [[ "${BASH_SOURCE[0]}" != "$0" ]]
}

_media_check_python_is_compatible() {
    "$1" -c 'import sys; raise SystemExit(0 if (3, 8) <= sys.version_info[:2] <= (3, 10) else 1)'
}

_media_check_cleanup() {
    local project_root="$1"
    local cleanup_status=0
    local search_root

    if [[ -z "$project_root" || "$project_root" == "/" ]]; then
        printf 'Refusing to clean an invalid project path: %s\n' "$project_root" >&2
        return 1
    fi

    if [[ ! -f "$project_root/pyproject.toml" || ! -d "$project_root/src/media_checker" ]]; then
        printf 'Refusing to clean an unrecognized project path: %s\n' "$project_root" >&2
        return 1
    fi

    rm -rf -- \
        "$project_root/build" \
        "$project_root/dist" \
        "$project_root/src/media_checker.egg-info" \
        "$project_root/.pytest_cache" \
        "$project_root/.mypy_cache" || cleanup_status=1

    for search_root in "$project_root/src" "$project_root/tests"; do
        [[ -d "$search_root" ]] || continue
        find "$search_root" \
            -type d -name '__pycache__' -prune -exec rm -rf -- {} + \
            || cleanup_status=1
        find "$search_root" \
            -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete \
            || cleanup_status=1
    done

    return "$cleanup_status"
}

_media_check_fail() {
    printf 'Setup failed: %s\n' "$1" >&2
    return 1
}

_media_check_main() {
    local script_dir
    local project_root
    local venv_python
    local base_python
    local wheel_files

    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || {
        _media_check_fail "Cannot locate the project directory"
        return 1
    }
    project_root="$script_dir"
    venv_python="$project_root/.venv/bin/python"

    _media_check_cleanup "$project_root" || return 1

    base_python="$(command -v python3)" || {
        _media_check_fail "python3 was not found"
        return 1
    }

    if ! _media_check_python_is_compatible "$base_python"; then
        _media_check_fail "python3 must be version 3.8 through 3.10"
        return 1
    fi

    if [[ -e "$project_root/.venv" ]]; then
        if [[ ! -x "$venv_python" ]] || ! _media_check_python_is_compatible "$venv_python"; then
            _media_check_fail ".venv is invalid or does not use Python 3.8 through 3.10; remove or rename it and run setup again"
            return 1
        fi
    else
        if ! "$base_python" -m venv "$project_root/.venv"; then
            _media_check_fail "Cannot create .venv"
            return 1
        fi
    fi

    if ! "$venv_python" -m pip install --upgrade pip build; then
        _media_check_cleanup "$project_root"
        _media_check_fail "Cannot install build tools"
        return 1
    fi

    if ! "$venv_python" -m build --outdir "$project_root/dist" "$project_root"; then
        _media_check_cleanup "$project_root"
        _media_check_fail "Cannot build media-checker"
        return 1
    fi

    wheel_files=("$project_root"/dist/media_checker-*.whl)

    if [[ ${#wheel_files[@]} -ne 1 || ! -f "${wheel_files[0]}" ]]; then
        _media_check_cleanup "$project_root"
        _media_check_fail "The build did not create exactly one media-checker wheel"
        return 1
    fi

    if ! "$venv_python" -m pip install --force-reinstall "${wheel_files[0]}"; then
        _media_check_cleanup "$project_root"
        _media_check_fail "Cannot install the media-checker wheel"
        return 1
    fi

    if ! _media_check_cleanup "$project_root"; then
        _media_check_fail "Cannot remove generated build files"
        return 1
    fi

    if _media_check_is_sourced; then
        # shellcheck disable=SC1091
        if ! source "$project_root/.venv/bin/activate"; then
            _media_check_fail "Cannot activate .venv"
            return 1
        fi
        printf 'media-checker is installed and .venv is active.\n'
    else
        printf 'media-checker is installed. Run: source %s/.venv/bin/activate\n' "$project_root"
    fi
}

if _media_check_is_sourced; then
    _media_check_main
    _media_check_status=$?
    unset -f \
        _media_check_is_sourced \
        _media_check_python_is_compatible \
        _media_check_cleanup \
        _media_check_fail \
        _media_check_main
    if [[ $_media_check_status -eq 0 ]]; then
        unset _media_check_status
        return 0
    fi
    unset _media_check_status
    return 1
else
    _media_check_main
    exit $?
fi
