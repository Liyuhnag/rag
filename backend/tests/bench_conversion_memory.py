# -*- coding: utf-8 -*-
"""比较 HEAD 旧收集器与当前实现：python tests/bench_conversion_memory.py。

使用磁盘假对象存储，不访问 S3/MinerU。RSS 为独立子进程峰值，含解释器。
"""

import argparse
import hashlib
import json
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import types
import zipfile
from datetime import datetime
from datetime import timezone
from pathlib import Path

BASELINE_REF = "a9b8818fcb7522735f71b7870aaea10eb52dae07"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))


class DiskStore:
    def __init__(self, root):
        self.root = root
        self.counter = 0

    def download_object(self, key, path):
        shutil.copyfile(self.root / key, path)

    def upload_file(self, key, path, content_type):
        target = self.root / "output" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def write_object(self, key, body, content_type):
        target = self.root / "output" / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)


def fixture(root, mib):
    unit = ("中文正文 " * 100 + "\n").encode()
    per_part = mib * 1024 * 1024 // 4
    for index in range(4):
        with zipfile.ZipFile(
            root / f"part-{index}.zip", "w", zipfile.ZIP_DEFLATED
        ) as archive:
            with archive.open("content.md", "w") as output:
                remaining = per_part
                while remaining:
                    chunk = unit if remaining >= len(unit) else b"x" * remaining
                    output.write(chunk)
                    remaining -= len(chunk)


def measure(mode, mib):
    with tempfile.TemporaryDirectory(prefix="conversion-bench-") as directory:
        root = Path(directory)
        fixture(root, mib)
        store = DiskStore(root)
        now = datetime.now(timezone.utc)
        start = time.monotonic()
        if mode == "baseline":
            source = subprocess.check_output(
                ["git", "show", f"{BASELINE_REF}:backend/services/parsing/collect.py"],
                cwd=ROOT,
            )
            module = types.ModuleType("baseline_collect")
            sys.modules[module.__name__] = module
            exec(compile(source, "baseline_collect.py", "exec"), module.__dict__)
            parts = [
                module.PartZip(
                    index + 1,
                    index + 1,
                    index + 1,
                    (root / f"part-{index}.zip").read_bytes(),
                )
                for index in range(4)
            ]
            result = module.collect_parts("bench", 1, "pdf", "original", parts, now)
            for item in result.objects:
                store.write_object(item.key, item.body, item.content_type)
            keys = result.result
        else:
            from services.parsing.collect import PartZip
            from services.parsing.collect import collect_parts

            parts = [
                PartZip(index + 1, index + 1, index + 1, f"part-{index}.zip")
                for index in range(4)
            ]
            keys = collect_parts("bench", 1, "pdf", "original", parts, now, store)
        digest = hashlib.sha256()
        with (root / "output" / keys.md_key).open("rb") as handle:
            while chunk := handle.read(65536):
                digest.update(chunk)
        print(
            json.dumps(
                {
                    "mode": mode,
                    "input_mib": mib,
                    "peak_rss_mib": round(
                        resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2
                    ),
                    "elapsed_seconds": round(time.monotonic() - start, 3),
                    "merged_sha256": digest.hexdigest(),
                }
            )
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["baseline", "current"])
    parser.add_argument("--mib", type=int, default=32)
    options = parser.parse_args()
    if options.mode:
        measure(options.mode, options.mib)
        return
    for mib in (32, 128):
        for mode in ("baseline", "current"):
            subprocess.run(
                [sys.executable, __file__, "--mode", mode, "--mib", str(mib)],
                cwd=ROOT,
                check=True,
            )


if __name__ == "__main__":
    main()
