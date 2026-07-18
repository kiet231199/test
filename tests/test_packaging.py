import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_supported_python_range_includes_python_3_10(self) -> None:
        pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

        self.assertIn('requires-python = ">=3.8,<3.11"', pyproject)

    def test_setup_script_builds_installs_activates_and_cleans(self) -> None:
        setup_path = PROJECT_ROOT / "setup.sh"
        setup      = setup_path.read_text(encoding = "utf-8")

        self.assertTrue(setup.startswith("#!/usr/bin/env bash\n"))
        self.assertIn("(3, 8) <= sys.version_info[:2] <= (3, 10)", setup)
        self.assertIn("-m venv", setup)
        self.assertIn("-m build", setup)
        self.assertIn("pip install --force-reinstall", setup)
        self.assertIn("source \"$project_root/.venv/bin/activate\"", setup)
        self.assertIn("_media_check_cleanup", setup)


@unittest.skipUnless(
    os.name == "posix" and shutil.which("bash") is not None,
    "Bash integration requires a POSIX host",
)
class SetupScriptIntegrationTests(unittest.TestCase):
    def test_setup_creation_reuse_activation_validation_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root         = Path(folder)
            project_root = root / "project"
            outside      = root / "outside"
            fake_bin     = root / "bin"
            project_root.mkdir()
            outside.mkdir()
            fake_bin.mkdir()
            (project_root / "src" / "media_checker").mkdir(parents = True)
            (project_root / "tests").mkdir()
            (project_root / "pyproject.toml").write_text(
                "[build-system]\nrequires = []\nbuild-backend = \"unused\"\n",
                encoding = "utf-8",
            )
            shutil.copy2(PROJECT_ROOT / "setup.sh", project_root / "setup.sh")

            fake_python = fake_bin / "python3"
            fake_python.write_text(
                """#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG"

if [[ "$1" == "-c" ]]; then
    if [[ "${FAKE_SYSTEM_INCOMPATIBLE:-0}" == "1" && "$0" == */bin/python3 ]]; then
        exit 1
    fi
    if [[ "${FAKE_VENV_INCOMPATIBLE:-0}" == "1" && "$0" == */.venv/bin/python ]]; then
        exit 1
    fi
    exit 0
fi

if [[ "$1" == "-m" && "$2" == "venv" ]]; then
    mkdir -p "$3/bin"
    cp "$0" "$3/bin/python"
    chmod +x "$3/bin/python"
    cat > "$3/bin/activate" <<EOF
VIRTUAL_ENV='$3'
export VIRTUAL_ENV
PATH="\$VIRTUAL_ENV/bin:\$PATH"
export PATH
EOF
    exit 0
fi

if [[ "$1" == "-m" && "$2" == "pip" ]]; then
    exit 0
fi

if [[ "$1" == "-m" && "$2" == "build" ]]; then
    mkdir -p "$4" "$5/build" "$5/src/media_checker.egg-info"
    touch "$4/media_checker-0.1.0-py3-none-any.whl"
    [[ "${FAKE_BUILD_FAIL:-0}" != "1" ]]
    exit $?
fi

exit 1
""",
                encoding = "utf-8",
            )
            fake_python.chmod(fake_python.stat().st_mode | stat.S_IXUSR)
            log_path = root / "python.log"
            environment = dict(os.environ)
            environment.update({
                "FAKE_LOG" : str(log_path),
                "OUTSIDE"  : str(outside),
                "PROJECT"  : str(project_root),
                "PATH"     : "{}{}{}".format(
                    fake_bin,
                    os.pathsep,
                    environment.get("PATH", ""),
                ),
            })
            command = """
cd "$OUTSIDE"
set -f
enable -n mapfile
source "$PROJECT/setup.sh"
status=$?
printf 'PWD=%s\nVIRTUAL_ENV=%s\n' "$PWD" "${VIRTUAL_ENV:-}"
exit "$status"
"""

            first = subprocess.run(
                ["bash", "-c", command],
                env = environment,
                check = False,
                capture_output = True,
                text = True,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            if os.name == "nt":
                self.assertRegex(first.stdout, r"(?m)^PWD=.*/outside$")
                self.assertRegex(
                    first.stdout,
                    r"(?m)^VIRTUAL_ENV=.*/project/\.venv$",
                )
            else:
                self.assertIn("PWD={}".format(outside), first.stdout)
                self.assertIn(
                    "VIRTUAL_ENV={}".format(project_root / ".venv"),
                    first.stdout,
                )
            self.assertIn("-m venv", log_path.read_text(encoding = "utf-8"))
            self._assert_build_outputs_removed(project_root)

            log_path.write_text("", encoding = "utf-8")
            reused = subprocess.run(
                ["bash", "-c", command],
                env = environment,
                check = False,
                capture_output = True,
                text = True,
            )

            self.assertEqual(reused.returncode, 0, reused.stderr)
            self.assertNotIn("-m venv", log_path.read_text(encoding = "utf-8"))

            system_environment = dict(environment)
            system_environment["FAKE_SYSTEM_INCOMPATIBLE"] = "1"
            invalid_system = subprocess.run(
                ["bash", "-c", command],
                env = system_environment,
                check = False,
                capture_output = True,
                text = True,
            )

            self.assertNotEqual(invalid_system.returncode, 0)
            self.assertIn("python3 must be version", invalid_system.stderr)

            incompatible_environment = dict(environment)
            incompatible_environment["FAKE_VENV_INCOMPATIBLE"] = "1"
            incompatible = subprocess.run(
                ["bash", "-c", command],
                env = incompatible_environment,
                check = False,
                capture_output = True,
                text = True,
            )

            self.assertNotEqual(incompatible.returncode, 0)
            self.assertIn(".venv is invalid", incompatible.stderr)
            self.assertTrue((project_root / ".venv").is_dir())

            failing_environment = dict(environment)
            failing_environment["FAKE_BUILD_FAIL"] = "1"
            failed = subprocess.run(
                ["bash", "-c", command],
                env = failing_environment,
                check = False,
                capture_output = True,
                text = True,
            )

            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("Cannot build media-checker", failed.stderr)
            self._assert_build_outputs_removed(project_root)

    def _assert_build_outputs_removed(self, project_root: Path) -> None:
        self.assertFalse((project_root / "build").exists())
        self.assertFalse((project_root / "dist").exists())
        self.assertFalse((project_root / "src" / "media_checker.egg-info").exists())


if __name__ == "__main__":
    unittest.main()
