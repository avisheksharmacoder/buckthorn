# setup.py
from setuptools import setup, find_packages, Extension
from Cython.Build import cythonize

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
        extra_compile_args=[
            "-O3",
            "-march=native",
            "-msse4.2",
            "-flto",
            "-fstack-protector-strong",
            "-D_FORTIFY_SOURCE=3",
            "-fno-strict-overflow",
            "-Wformat",
            "-Wformat-security",
            "-Werror=format-security",
            "-Wall",
            "-Wno-unused-function",
        ],
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
    ext_modules=cythonize(
        ext_modules,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
        },
        annotate=True,
    ),
)
