"""Prepare the pinned CPU search model. Downloads require an explicit flag."""

import argparse
from pathlib import Path

MODEL = "BAAI/bge-m3"
REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
FILES = [
    "config.json", "config_sentence_transformers.json", "modules.json",
    "sentence_bert_config.json", "special_tokens_map.json", "tokenizer.json",
    "tokenizer_config.json", "sentencepiece.bpe.model", "1_Pooling/config.json",
    "pytorch_model.bin",
]


def main():
    from huggingface_hub import HfApi, snapshot_download

    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--allow-large-download", action="store_true")
    args = parser.parse_args()
    if args.download:
        info = HfApi().model_info(MODEL, revision=REVISION, files_metadata=True)
        sizes = {item.rfilename: item.size for item in info.siblings}
        if any(sizes.get(name) is None for name in FILES):
            raise SystemExit("Model manifest is incomplete; download cancelled.")
        total = sum(sizes[name] for name in FILES)
        print(f"Pinned model files: at most {total} bytes before cache reuse.", flush=True)
        if total > 5_000_000_000 and not args.allow_large_download:
            raise SystemExit("More than 5 GB requires --allow-large-download after owner approval.")
    snapshot = Path(snapshot_download(
        MODEL, revision=REVISION, allow_patterns=FILES,
        local_files_only=not args.download, max_workers=2,
    ))
    if snapshot.name != REVISION or any(not (snapshot / name).is_file() for name in FILES):
        raise SystemExit("Pinned model is missing files. Run explicitly with --download.")
    print(f"MODEL_READY revision={REVISION} files={len(FILES)}")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"MODEL_NOT_READY ({type(error).__name__}); no credentials printed.") from None
