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
            "buckthorn/c_vendor/picohttpparser.c",
            "buckthorn/c_vendor/rax.c",
            "buckthorn/c_vendor/yyjson.c",
        ],
        include_dirs=[
            "buckthorn",
            "buckthorn/c_src",
            "buckthorn/c_vendor",
        ],
        libraries=["uv", "pthread"],
        extra_compile_args=[
            "-O3",
            "-march=native",
            "-msse4.2",
            "-flto",
            "-Wall",
            "-Wno-unused-function",
        ],
        extra_link_args=["-flto"],
    )
]

setup(
    name="buckthorn",
    version="0.5.0",
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
