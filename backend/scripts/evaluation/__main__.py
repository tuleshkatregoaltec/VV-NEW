"""Local AI benchmark execution, evaluation and explicit sanitized export."""

import argparse
import json

from scripts.evaluation.dataset import load_benchmark
from scripts.evaluation.inventory import build_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["inventory", "dataset", "run", "assess", "compare", "export"]
    )
    parser.add_argument("--directory")
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--data-context", default="local-mutable-warehouse")
    parser.add_argument("--bindings")
    parser.add_argument("--references")
    parser.add_argument("--reviews")
    parser.add_argument("--baseline")
    parser.add_argument("--candidate")
    parser.add_argument("--assessment")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args()
    if args.command == "export":
        from pathlib import Path

        from scripts.evaluation.langsmith import export_run

        if not args.directory or not args.assessment:
            parser.error("export requires --directory and --assessment")
        print(
            json.dumps(
                export_run(Path(args.directory), Path(args.assessment), args.upload), indent=2
            )
        )
        return
    if args.command == "assess":
        from pathlib import Path

        from scripts.evaluation.evaluate import assess_run

        if not args.directory:
            parser.error("assess requires --directory")
        path, result = assess_run(Path(args.directory), args.references, args.reviews)
        from scripts.evaluation.report import write_report

        print(f"Private assessment: {path}")
        print(f"Private report: {write_report(Path(args.directory), result)}")
        for case in result["cases"]:
            print(
                json.dumps(
                    {
                        "case_id": case["case_id"],
                        "checks": {name: check["status"] for name, check in case["checks"].items()},
                    }
                )
            )
        return
    if args.command == "compare":
        from pathlib import Path

        from scripts.evaluation.evaluate import compare

        if not args.baseline or not args.candidate:
            parser.error("compare requires --baseline and --candidate")
        result = compare(
            json.loads(Path(args.baseline).read_text()),
            json.loads(Path(args.candidate).read_text()),
        )
        print(json.dumps(result, indent=2))
        raise SystemExit(2 if not result["comparable"] else 1 if result["regressions"] else 0)
    if args.command == "run":
        import asyncio

        from scripts.evaluation.run import run_benchmark

        asyncio.run(
            run_benchmark(
                args.directory,
                args.case_ids,
                data_context=args.data_context,
                bindings_path=args.bindings,
            )
        )
        return
    if args.command == "inventory":
        result = build_inventory()
    else:
        benchmark = load_benchmark()
        result = {"sha256": benchmark.digest, **benchmark.model_dump()}
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
