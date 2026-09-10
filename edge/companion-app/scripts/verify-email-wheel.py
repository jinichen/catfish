"""Reject incomplete/stale email wheels before packaging an MSI (no imports/COM)."""
import sys
import zipfile
from pathlib import Path


def verify(stage: Path, source: Path) -> None:
    wheels = list(stage.glob("catfish_email-*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"Expected one email wheel, found {len(wheels)}")
    package = source / "src" / "catfish_email"
    with zipfile.ZipFile(wheels[0]) as archive:
        for required in ("discovery.py", "adapters/foxmail_discovery.py", "__main__.py"):
            if not (package / required).is_file():
                raise ValueError(f"Missing discovery source: {required}")
        for path in package.rglob("*.py"):
            name = "catfish_email/" + path.relative_to(package).as_posix()
            if archive.read(name) != path.read_bytes():
                raise ValueError(f"Stale wheel content: {name}")
    print(f"Verified email wheel against source: {wheels[0].name}")


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
