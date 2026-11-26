#!/usr/bin/env python3
"""Package a Git tree as the executable Decktil download."""
import argparse
import gzip
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def build(output, ref):
    # A tree has no commit ID or timestamp. Normalize the time, permissions
    # and gzip header so tagging or merging identical source keeps the bundle.
    tree = subprocess.check_output(
        ["git", "rev-parse", "--verify", "--end-of-options", ref + "^{tree}"],
        cwd=ROOT, text=True,
    ).strip()
    command = ["git", "-c", "tar.umask=0022", "archive", "--format=tar", "--prefix=Decktil/",
               "--mtime=1970-01-01T00:00:00Z", tree]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        temporary = Path(staging) / "bundle"
        with temporary.open("wb") as raw, gzip.GzipFile(
            filename="", mode="wb", compresslevel=9, mtime=0, fileobj=raw,
        ) as compressed, subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE) as archiver:
            with archiver.stdout as source:
                shutil.copyfileobj(source, compressed)
            if archiver.wait():
                raise SystemExit("git archive failed")
        temporary.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="path to the Decktil.tar.gz to write")
    parser.add_argument("ref", nargs="?", default="HEAD", help="commit, tag or tree (default: HEAD)")
    args = parser.parse_args()
    build(args.output, args.ref)
