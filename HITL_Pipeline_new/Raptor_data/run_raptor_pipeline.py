import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

from raptor_solver import RaptorRouter


def main():
    parser = argparse.ArgumentParser(description="Unified RAPTOR pipeline orchestrator.")
    parser.add_argument("--with-benchmarks", action="store_true", help="Run benchmark gate during build.")
    parser.add_argument(
        "--skip-bundle",
        action="store_true",
        help="Skip merged bundle generation (raptor_bundle.json).",
    )
    parser.add_argument(
        "--benchmark-init",
        action="store_true",
        help="Initialize benchmark snapshot before build.",
    )
    args = parser.parse_args()

    base_dir = os.path.dirname(os.path.abspath(__file__))
    build_script = os.path.join(base_dir, "build_raptor_data.py")
    suite_path = os.path.join(base_dir, "raptor_benchmark_suite.json")

    env = os.environ.copy()
    env["RUN_BENCHMARKS"] = "1" if args.with_benchmarks else "0"
    env["MERGE_RAPTOR_BUNDLE"] = "0" if args.skip_bundle else "1"

    if args.benchmark_init:
        if not os.path.exists(suite_path):
            raise SystemExit(f"Benchmark suite not found: {suite_path}")
        with open(suite_path, "r", encoding="utf-8") as f:
            suite = json.load(f)
        cases = suite.get("cases") or []
        if not cases:
            raise SystemExit("Benchmark suite has no cases.")

        print("Initializing benchmark snapshot...")
        router = RaptorRouter(base_dir)

        def normalize_result(result):
            status = result.get("status", "UNKNOWN")
            norm = {"status": status}
            if status != "SUCCESS":
                return norm
            options = result.get("options", []) or []
            norm["option_count"] = len(options)
            norm["has_direct_road_access"] = any(o.get("type") == "DIRECT ROAD ACCESS" for o in options)
            if options:
                best = min(options, key=lambda x: x.get("arrival_mins", 10**9))
                itinerary = best.get("itinerary", []) or []
                norm.update(
                    {
                        "best_arrival_mins": best.get("arrival_mins"),
                        "best_duration_mins": best.get("duration_mins"),
                        "best_transfers": best.get("transfers"),
                        "best_type": best.get("type"),
                        "best_itinerary_types": [leg.get("type") for leg in itinerary[:5]],
                    }
                )
            return norm

        observed = {}
        for case in cases:
            result = router.solve(
                case["origin_lat"],
                case["origin_lng"],
                case["dest_lat"],
                case["dest_lng"],
                case["departure_time_mins"],
                case.get("max_rounds", 2),
            )
            observed[case["id"]] = normalize_result(result)

        suite["snapshot"] = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "data_dir": os.path.abspath(base_dir),
            "case_count": len(cases),
            "results": observed,
        }
        with open(suite_path, "w", encoding="utf-8") as f:
            json.dump(suite, f, indent=2)
        print(f"Snapshot updated in suite: {suite_path} (cases={len(cases)})")

    cmd_build = [sys.executable, build_script]
    print("Running RAPTOR build pipeline...")
    res_build = subprocess.run(cmd_build, env=env, check=False)
    raise SystemExit(res_build.returncode)


if __name__ == "__main__":
    main()
