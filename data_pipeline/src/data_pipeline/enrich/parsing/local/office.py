from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def convert_office(source: Path, destination_dir: Path, target: str) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ir-office-") as profile:
        subprocess.run(
            [
                "libreoffice",
                "--headless",
                f"-env:UserInstallation=file://{profile}",
                "--convert-to",
                target,
                "--outdir",
                str(destination_dir),
                str(source),
            ],
            check=True,
            timeout=180,
            capture_output=True,
        )
    result = destination_dir / f"{source.stem}.{target.rsplit(':', maxsplit=1)[-1].lower()}"
    if not result.exists():
        matches = list(destination_dir.glob(f"{source.stem}.*"))
        if not matches:
            msg = f"LibreOffice did not create output for {source}"
            raise RuntimeError(msg)
        result = matches[0]
    return result
