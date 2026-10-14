# buckthorn/cli.py
import argparse
import importlib
import os
import signal
import sys
from pathlib import Path
from typing import Any, Sequence
from buckthorn.compiler import transpile_module_to_pyx, compile_pyx_module


def parse_cli_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="buckthorn",
        description="Buckthorn High-Performance Compiled C/Cython Web Framework CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. `buckthorn dev`
    dev_p = sub.add_parser("dev", help="Run Buckthorn app in development mode")
    dev_p.add_argument("target", help="Module:app target (e.g. main:app)")
    dev_p.add_argument("--host", default="127.0.0.1")
    dev_p.add_argument("--port", type=int, default=8000)
    dev_p.add_argument("--reload", action="store_true")

    # 2. `buckthorn build`
    build_p = sub.add_parser("build", help="Transpile Python app to Cython and compile to .so")
    build_p.add_argument("source", help="Path to Python source file (e.g. main.py)")
    build_p.add_argument("--prod", action="store_true", help="Enable production Cython optimizations")
    build_p.add_argument("--out-dir", default="dist_buckthorn", help="Output directory for .pyx and .so")

    # 3. `buckthorn run`
    run_p = sub.add_parser("run", help="Run Buckthorn app in production mode with SO_REUSEPORT workers")
    run_p.add_argument("target", help="Module:app target (e.g. main:app)")
    run_p.add_argument("--host", default="0.0.0.0")
    run_p.add_argument("--port", type=int, default=8000)
    run_p.add_argument("--workers", type=int, default=1)

    return parser.parse_args(argv)


def _load_target_app(target: str) -> Any:
    if ":" not in target:
        raise ValueError("Target must be formatted as 'module:app'")
    mod_name, app_attr = target.split(":", 1)
    if mod_name.endswith(".py"):
        mod_name = mod_name[:-3]
    sys.path.insert(0, os.getcwd())
    mod = importlib.import_module(mod_name)
    return getattr(mod, app_attr)


def build_command(args: argparse.Namespace) -> Path:
    src_path = Path(args.source).resolve()
    if not src_path.exists():
        raise FileNotFoundError(f"Source file not found: {src_path}")

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    mod_name = src_path.stem
    pyx_code = transpile_module_to_pyx(src_path.read_text(encoding="utf-8"))
    compile_pyx_module(pyx_code, module_name=mod_name, workdir=out_dir)

    so_files = list(out_dir.glob(f"{mod_name}*.so"))
    if not so_files:
        raise RuntimeError("Compilation finished but no .so binary was found")
    return so_files[0]


def run_command(args: argparse.Namespace) -> None:
    workers = max(1, int(getattr(args, "workers", 1)))
    if workers == 1:
        app = _load_target_app(args.target)
        app.run(host=args.host, port=args.port)
        return

    pids: list[int] = []
    for _ in range(workers):
        pid = os.fork()
        if pid == 0:
            # Child worker binds to the same port via Linux kernel SO_REUSEPORT
            app = _load_target_app(args.target)
            app.run(host=args.host, port=args.port)
            os._exit(0)
        else:
            pids.append(pid)

    def _sig_handler(signum: int, frame: Any) -> None:
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        sys.exit(0)

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)
    for pid in pids:
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_cli_args(argv)
    if args.command == "build":
        so_path = build_command(args)
        print(f"[Buckthorn] Compiled production extension: {so_path}")
    elif args.command in ("dev", "run"):
        run_command(args)


if __name__ == "__main__":
    main()
