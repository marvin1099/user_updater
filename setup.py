from __future__ import annotations

import os
import shutil
import sys
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext

ROOT = Path(__file__).parent.resolve()


class CMakeExtension(Extension):
    def __init__(self, name: str) -> None:
        super().__init__(name, sources=[])


class CMakeBuildExt(build_ext):
    def build_extension(self, extension: Extension) -> None:
        cmake = shutil.which("cmake")
        if cmake is None:
            raise RuntimeError("CMake is required to build the optional LayerShellQt bridge")

        build_dir = Path(self.build_temp) / "layer-shell"
        output_dir = Path(self.get_ext_fullpath(extension.name)).resolve().parent
        config = "Debug" if self.debug else "Release"
        configure = [
            cmake,
            "-S",
            str(ROOT / "native" / "layer-shell"),
            "-B",
            str(build_dir),
            f"-DPython3_EXECUTABLE={sys.executable}",
            f"-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={output_dir}",
            f"-DCMAKE_BUILD_TYPE={config}",
        ]
        build = [cmake, "--build", str(build_dir), "--config", config, "--target", "_layer_shell"]
        self.spawn(configure)
        self.spawn(build)


def _has_layer_shell_development_files() -> bool:
    if shutil.which("cmake") is None:
        return False
    python_include = Path(sysconfig.get_path("include"))
    if not (python_include / "Python.h").is_file():
        return False

    package_config = "LayerShellQtConfig.cmake"
    search_dirs: set[Path] = set()
    layer_shell_dir = os.environ.get("LayerShellQt_DIR")
    if layer_shell_dir:
        search_dirs.add(Path(layer_shell_dir))
    for prefix in os.environ.get("CMAKE_PREFIX_PATH", "").split(os.pathsep):
        if prefix:
            root = Path(prefix)
            search_dirs.update(
                {
                    root / "lib" / "cmake" / "LayerShellQt",
                    root / "lib64" / "cmake" / "LayerShellQt",
                    root / "share" / "cmake" / "LayerShellQt",
                }
            )
            search_dirs.update(root.glob("lib/*/cmake/LayerShellQt"))

    search_dirs.update(
        {
            Path("/usr/lib/cmake/LayerShellQt"),
            Path("/usr/lib64/cmake/LayerShellQt"),
            Path("/usr/local/lib/cmake/LayerShellQt"),
            Path("/usr/local/lib64/cmake/LayerShellQt"),
        }
    )
    search_dirs.update(Path("/usr/lib").glob("*/cmake/LayerShellQt"))
    search_dirs.update(Path("/usr/local/lib").glob("*/cmake/LayerShellQt"))
    return any((directory / package_config).is_file() for directory in search_dirs)


setup(
    ext_modules=(
        [CMakeExtension("user_updater.gui._layer_shell")]
        if _has_layer_shell_development_files()
        else []
    ),
    cmdclass={"build_ext": CMakeBuildExt},
)
