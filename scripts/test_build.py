#!/usr/bin/env python3
"""Exercise build.sh in disposable Git repos; make is a firmware-output stub."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPTS = Path(__file__).resolve().parent


class BuildScriptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="px4 build test ")
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.board = self.repo / "boards/vendor/flight"
        self.board.mkdir(parents=True)
        self.write("boards/vendor/flight/default.px4board", "CONFIG_BASE=y\n")
        self.write("boards/vendor/flight/custom.px4board", "CONFIG_OVERLAY=y\n")
        self.write("boards/vendor/flight/bootloader_secure.px4board", "CONFIG_BOOT=y\n")
        self.write("boards/vendor/flight/nuttx-config/nsh/defconfig", "CONFIG_NSH=y\n")
        self.write("boards/vendor/flight/nuttx-config/bootloader/defconfig", "CONFIG_BOOT=y\n")
        self.write("boards/px4/sitl/default.px4board", "CONFIG_SIM=y\n")
        self.write("src/modules/mc_autotune_attitude_control/Kconfig",
                   "menuconfig MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL\n"
                   "config MC_AUTOTUNE_EXPERIMENTAL\n")
        self.write("Makefile", "# The test uses a stub make.\n")
        self.write("payload", "committed\n")
        self.write(".gitignore", "build/\n")
        for name in ("build.sh", "build-configs.sh"):
            self.write(f"scripts/{name}", (SCRIPTS / name).read_text())
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Build Test")
        self.git("config", "user.email", "build-test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.commit = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "v1.0.0")
        self.git("branch", "local-only")
        self.git("update-ref", "refs/remotes/origin/main", self.commit)
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        # Keep checks independent of installed PX4 Python modules and compilers.
        for tool in ("python", "cmake", "c++"):
            path = self.bin / tool
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        make = self.bin / "make"
        make.write_text("""#!/usr/bin/env bash
set -eu
[[ $1 == --no-print-directory && $2 == -C ]]
cd -- "$3"
target=$4
mkdir -p "build/$target"
cat payload > "build/$target/$target.elf"
if [[ -f local-file ]]; then cat local-file >> "build/$target/$target.elf"; fi
""")
        make.chmod(0o755)
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        PX4_PYTHON=str(self.bin / "python"))
        self.state = self.repo / "build/.build-sh"

    def write(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       text=True, stderr=subprocess.STDOUT)

    def run_build(self, *args, ok=True, target="vendor_flight_default"):
        result = subprocess.run(
            ["bash", str(self.repo / "scripts/build.sh"), "--target", target, *args],
            env=self.env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            cwd=self.temp.name, timeout=20,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout

    def profile(self, name="saved", variant="default"):
        return self.board / "custom-configs" / variant / name

    def test_current_build_uses_dirty_and_untracked_files(self):
        self.write("payload", "uncommitted\n")
        self.write("local-file", "untracked\n")
        output = self.run_build("--action", "build")
        elf = self.repo / "build/vendor_flight_default/vendor_flight_default.elf"
        self.assertEqual(elf.read_text(), "uncommitted\nuntracked\n")
        self.assertIn("Текущая ветка: main", output)
        self.assertIn("незакоммиченные", output)
        self.assertFalse((self.state / "sources").exists())

    def test_branch_and_tag_builds_are_isolated(self):
        self.git("checkout", "-q", "local-only")
        self.write("payload", "unpushed commit\n")
        self.git("commit", "-qam", "local commit")
        branch_commit = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "-q", "main")
        self.write("payload", "dirty main\n")
        for ref, sha, expected in (("local-only", branch_commit, "unpushed commit\n"),
                                   ("v1.0.0", self.commit, "committed\n")):
            with self.subTest(ref=ref):
                self.run_build("--ref", ref, "--action", "build")
                source = self.state / "sources" / sha
                elf = source / "build/vendor_flight_default/vendor_flight_default.elf"
                self.assertEqual(elf.read_text(), expected)
                self.assertEqual((self.repo / "payload").read_text(), "dirty main\n")
                self.assertEqual(self.git("branch", "--show-current").strip(), "main")
        manifests = list(self.state.glob("artifacts/vendor_flight_default/*/SUCCESS"))
        self.assertEqual(len(manifests), 2)
        self.assertTrue(any("Source ref: local-only" in f.read_text() for f in manifests))

    def test_dry_run_and_info_do_not_create_worktrees(self):
        before = self.git("status", "--porcelain", "--untracked-files=all")
        for action in ("build", "kernel-config", "info"):
            output = self.run_build("--ref", "v1.0.0", "--action", action, "--dry-run")
            self.assertIn(str(self.state / "sources" / self.commit), output)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), before)

    def test_lists_refs_and_revision_specific_targets(self):
        output = self.run_build("--list-refs")
        for item in ("current", "main", "local-only", "refs/tags/v1.0.0", "refs/remotes/origin/main"):
            self.assertIn(item, output)
        self.write("boards/vendor/flight/new.px4board", "CONFIG_NEW=y\n")
        self.assertIn("vendor_flight_new", self.run_build("--list-targets"))
        self.assertNotIn("vendor_flight_new", self.run_build("--list-targets", "--ref", "v1.0.0"))

    def test_invalid_ref_and_target_fail_without_writes(self):
        self.run_build("--ref", "missing", "--action", "build", ok=False)
        self.run_build("--ref", "v1.0.0", "--action", "build", target="vendor_flight_missing", ok=False)
        self.assertFalse(self.state.exists())

    def test_profile_round_trip_and_backup(self):
        self.run_build("--action", "save-config", "--profile", "saved")
        self.assertEqual((self.profile() / "default.px4board").read_text(), "CONFIG_BASE=y\n")
        self.assertEqual((self.profile() / "nuttx-config/nsh/defconfig").read_text(), "CONFIG_NSH=y\n")
        self.write("boards/vendor/flight/default.px4board", "CONFIG_CHANGED=y\n")
        self.write("boards/vendor/flight/nuttx-config/nsh/defconfig", "CONFIG_CHANGED=y\n")
        self.run_build("--action", "load-config", "--profile", "saved")
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_BASE=y\n")
        self.assertEqual((self.board / "nuttx-config/nsh/defconfig").read_text(), "CONFIG_NSH=y\n")
        backup = next(self.state.glob("backups/*-load-*/default.px4board"))
        self.assertEqual(backup.read_text(), "CONFIG_CHANGED=y\n")
        self.assertEqual((backup.parent / "nuttx-config/nsh/defconfig").read_text(), "CONFIG_CHANGED=y\n")
        self.assertIn("saved", self.run_build("--action", "list-configs"))

    def test_overlay_profile_includes_base(self):
        self.run_build("--action", "save-config", "--profile", "overlay", target="vendor_flight_custom")
        profile = self.profile("overlay", "custom")
        self.assertTrue((profile / "default.px4board").is_file())
        self.write("boards/vendor/flight/default.px4board", "CONFIG_OTHER=y\n")
        self.write("boards/vendor/flight/custom.px4board", "CONFIG_OTHER=y\n")
        self.run_build("--action", "load-config", "--profile", "overlay", target="vendor_flight_custom")
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_BASE=y\n")
        self.assertEqual((self.board / "custom.px4board").read_text(), "CONFIG_OVERLAY=y\n")

    def test_bootloader_profile_uses_bootloader_kernel(self):
        self.run_build("--action", "save-config", "--profile", "boot", target="vendor_flight_bootloader_secure")
        profile = self.profile("boot", "bootloader_secure")
        self.assertTrue((profile / "nuttx-config/bootloader/defconfig").is_file())
        self.assertFalse((profile / "nuttx-config/nsh/defconfig").exists())
        self.assertFalse((profile / "default.px4board").exists())

    def test_sitl_profile_has_no_nuttx(self):
        self.run_build("--action", "save-config", "--profile", "sim", target="px4_sitl_default")
        profile = self.repo / "boards/px4/sitl/custom-configs/default/sim"
        self.assertTrue((profile / "default.px4board").is_file())
        self.assertFalse((profile / "nuttx-config").exists())
        self.run_build("--action", "load-config", "--profile", "sim", target="px4_sitl_default")

    def test_profile_is_shared_with_other_revisions(self):
        self.write("boards/vendor/flight/default.px4board", "CONFIG_LOCAL=y\n")
        self.run_build("--action", "save-config", "--profile", "saved")
        self.run_build("--ref", "v1.0.0", "--action", "load-config", "--profile", "saved")
        source_board = self.state / "sources" / self.commit / "boards/vendor/flight"
        self.assertEqual((source_board / "default.px4board").read_text(), "CONFIG_LOCAL=y\n")
        self.run_build("--ref", "v1.0.0", "--action", "save-config", "--profile", "from-tag")
        self.assertTrue((self.profile("from-tag") / "default.px4board").is_file())
        self.assertFalse((source_board / "custom-configs").exists())

    def test_dry_run_profiles_do_not_write(self):
        self.run_build("--action", "save-config", "--profile", "saved", "--dry-run")
        self.assertFalse(self.profile().exists())
        self.assertFalse(self.state.exists())
        self.run_build("--action", "save-config", "--profile", "saved")
        self.write("boards/vendor/flight/default.px4board", "CONFIG_OTHER=y\n")
        self.run_build("--action", "load-config", "--profile", "saved", "--dry-run")
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_OTHER=y\n")
        self.assertFalse((self.state / "backups").exists())

    def test_bad_profiles_do_not_replace_configuration(self):
        for name in ("../escape", "/tmp/escape", "", "a/b"):
            self.run_build("--action", "save-config", "--profile", name, ok=False)
        self.run_build("--action", "save-config", "--profile", "saved")
        self.write("boards/vendor/flight/default.px4board", "CONFIG_OTHER=y\n")
        self.run_build("--action", "save-config", "--profile", "saved", ok=False)
        self.assertEqual((self.profile() / "default.px4board").read_text(), "CONFIG_BASE=y\n")
        (self.profile() / "nuttx-config/nsh/defconfig").unlink()
        self.run_build("--action", "load-config", "--profile", "saved", ok=False)
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_OTHER=y\n")
        (self.profile() / "target").write_text("vendor_other_default\n")
        self.run_build("--action", "load-config", "--profile", "saved", ok=False)
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_OTHER=y\n")

    def test_autotune_modes_preserve_overlay_and_backup(self):
        base = "CONFIG_MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL=y\nCONFIG_MC_AUTOTUNE_EXPERIMENTAL=y\n"
        self.write("boards/vendor/flight/default.px4board", base)
        overlay = self.board / "custom.px4board"
        for mode in ("standard", "experimental", "disabled"):
            previous = overlay.read_text()
            self.run_build("--action", "autotune-config", "--autotune", mode,
                           target="vendor_flight_custom")
            self.assertEqual((self.board / "default.px4board").read_text(), base)
            self.assertIn("CONFIG_OVERLAY=y\n", overlay.read_text())
            enabled = "n" if mode == "disabled" else "y"
            experimental = "y" if mode == "experimental" else "n"
            self.assertIn(f"CONFIG_MODULES_MC_AUTOTUNE_ATTITUDE_CONTROL={enabled}\n", overlay.read_text())
            self.assertIn(f"CONFIG_MC_AUTOTUNE_EXPERIMENTAL={experimental}\n", overlay.read_text())
            self.assertEqual(overlay.read_text().count("CONFIG_MC_AUTOTUNE_EXPERIMENTAL="), 1)
            backups = list(self.state.glob("backups/*-autotune-*/custom.px4board"))
            self.assertTrue(any(path.read_text() == previous for path in backups))
            self.assertIn(f"Автотюн: {mode}", self.run_build("--action", "info", target="vendor_flight_custom"))

    def test_autotune_dry_run_and_revision_isolation(self):
        for ref in ("current", "v1.0.0"):
            self.run_build("--ref", ref, "--action", "autotune-config",
                           "--autotune", "experimental", "--dry-run")
        self.assertFalse(self.state.exists())
        self.run_build("--ref", "v1.0.0", "--action", "autotune-config", "--autotune", "experimental")
        config = self.state / "sources" / self.commit / "boards/vendor/flight/default.px4board"
        self.assertIn("CONFIG_MC_AUTOTUNE_EXPERIMENTAL=y", config.read_text())
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_BASE=y\n")

    def test_autotune_rejects_unsupported_targets_and_options(self):
        self.run_build("--action", "autotune-config", "--autotune", "experimental",
                       target="vendor_flight_bootloader_secure", ok=False)
        self.run_build("--action", "autotune-config", ok=False)
        self.run_build("--action", "autotune-config", "--autotune", "invalid", ok=False)
        self.run_build("--action", "build", "--autotune", "experimental", ok=False)
        self.write("src/modules/mc_autotune_attitude_control/Kconfig", "config OLD_AUTOTUNE\n")
        self.run_build("--action", "autotune-config", "--autotune", "experimental", ok=False)
        self.assertFalse(self.state.exists())
        self.assertEqual((self.board / "default.px4board").read_text(), "CONFIG_BASE=y\n")


if __name__ == "__main__":
    unittest.main()
