# setup.py
import os
import platform
from setuptools import setup, find_packages, Extension
from Cython.Build import cythonize

portable_wheel = os.environ.get("BUCKTHORN_PORTABLE_WHEEL", "0") == "1"
arch = platform.machine()

extra_compile_args = [
    "-O3",
    "-flto",
    "-fstack-protector-strong",
    "-D_FORTIFY_SOURCE=2",
    "-DYYJSON_DISABLE_NON_STANDARD=1",
    "-DYYJSON_DISABLE_INCR_READER=1",
    "-DYYJSON_DISABLE_FILE=1",
    "-DYYJSON_DISABLE_UTILS=1",
    "-DYYJSON_READER_DEPTH_LIMIT=64",
    "-DYYJSON_WRITER_DEPTH_LIMIT=64",
    "-Wformat",
    "-Wformat-security",
    "-Werror=format-security",
    "-Wall",
    "-Wno-unused-function",
]

if not portable_wheel:
    extra_compile_args.append("-march=native")

if arch in ("x86_64", "AMD64"):
    extra_compile_args.append("-msse4.2")
elif arch in ("aarch64", "arm64") and portable_wheel:
    extra_compile_args.append("-march=armv8-a")

ext_modules = [
    Extension(
        name="buckthorn._core",
        sources=[
            "buckthorn/_core.pyx",
            "buckthorn/c_src/bt_http.c",
            "buckthorn/c_src/bt_params.c",
            "buckthorn/c_src/bt_json.c",
            "buckthorn/c_src/bt_logger.c",
            "buckthorn/c_src/bt_security.c",
            "buckthorn/c_src/bt_middleware.c",
            "buckthorn/c_src/bt_realtime.c",
            "buckthorn/c_src/bt_audit.c",
            "buckthorn/c_vendor/picohttpparser.c",
            "buckthorn/c_vendor/rax.c",
            "buckthorn/c_vendor/yyjson.c",
        ],
        include_dirs=[
            "buckthorn",
            "buckthorn/c_src",
            "buckthorn/c_vendor",
        ],
        libraries=["uv", "pthread", "crypto"],
        extra_compile_args=extra_compile_args,
        extra_link_args=[
            "-flto",
            "-Wl,-z,relro,-z,now",
        ],
    )
]

setup(
    name="buckthorn",
    version="1.1.0",
    packages=find_packages(),
    package_data={"buckthorn": ["py.typed", "*.pyi"]},
    include_package_data=True,
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "initializedcheck": False,
            "infer_types": True,
        },
        annotate=True,
    ),
)
