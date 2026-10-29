"""Download one CORD split (parquet: images + labels) into data/cord/.

Usage: python scripts/download_cord.py [test|validation]

The test split (100 receipts, ~234 MB) is the held-out evaluation set: prompts
are never tuned on it. The 800-receipt train split (~1.8 GB) is not needed.
"""

import sys
import urllib.request
from pathlib import Path

BASE_URL = "https://huggingface.co/api/datasets/naver-clova-ix/cord-v2/parquet/default"
SPLITS = ("test", "validation")


def main() -> None:
    split = sys.argv[1] if len(sys.argv) > 1 else "test"
    if split not in SPLITS:
        sys.exit(f"split must be one of {SPLITS}")

    target = Path("data/cord") / f"{split}.parquet"
    if target.exists():
        print(f"{target} already exists, skipping")
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    print(f"downloading {split} split to {target} ...")
    urllib.request.urlretrieve(f"{BASE_URL}/{split}/0.parquet", partial)
    # Rename only once complete, so an interrupted download is never mistaken
    # for a finished one.
    partial.rename(target)
    print(f"done: {target.stat().st_size / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
