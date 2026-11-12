"""Build static/app.css from styles/input.css with Tailwind's standalone CLI (installed by the pytailwindcss package,
so no Node.js is needed). The built file is committed, so servers never need to run this.

    python -m app.web.build_css            # one minified build
    python -m app.web.build_css --watch    # rebuild while editing templates
"""
import subprocess
import sys
from pathlib import Path

WEB = Path(__file__).resolve().parent
TAILWIND_VERSION = "v4.3.3"


def tailwind_binary() -> Path:
    import pytailwindcss
    from pytailwindcss import install

    bin_dir = Path(pytailwindcss.__file__).parent / "bin" / TAILWIND_VERSION
    existing = next(bin_dir.glob("tailwindcss*"), None) if bin_dir.exists() else None
    return existing or Path(install(version=TAILWIND_VERSION))  # first run downloads the CLI


def main() -> int:
    args = [str(tailwind_binary()), "-i", str(WEB / "styles/input.css"), "-o", str(WEB / "static/app.css"),
            "--minify", *sys.argv[1:]]
    return subprocess.call(args, cwd=WEB / "styles")


if __name__ == "__main__":
    raise SystemExit(main())
