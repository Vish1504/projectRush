
import argparse
import json
import math
import statistics
from pathlib import Path


# Calculate latency statistics for a particular stage.
def metrics(values):

    # Sort the measurements to calculate percentiles.
    values = sorted(values)

    return {
        "count": len(values),

        "mean_ms": round(
            statistics.mean(values), 3
        ),

        "p50_ms": round(
            values[math.ceil(len(values) * 0.50) - 1],
            3,
        ),

        "p95_ms": round(
            values[math.ceil(len(values) * 0.95) - 1],
            3,
        ),

        "p99_ms": round(
            values[math.ceil(len(values) * 0.99) - 1],
            3,
        ),
    }


def main():

    # Accept the trial number and server log file.
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--trial",
        type=int,
        required=True,
    )

    parser.add_argument(
        "--log",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    # Locate the directory containing the benchmark results.
    trial = (
        Path(__file__).resolve().parent
        / "results"
        / f"trial_{args.trial:02d}"
    )

    # --------------------------------------------------
    # Read the profiling records from the server logs
    # --------------------------------------------------

    profiles = {}

    for line in args.log.read_text().splitlines():

        # Ignore regular Uvicorn logs and unrelated messages.
        if "RUSH_PROFILE {" not in line:
            continue

        # Extract the JSON portion of the log entry.
        record = json.loads(
            line.split("RUSH_PROFILE ", 1)[1]
        )

        # Group profiling records using their request IDs.
        profiles.setdefault(
            record["request_id"],
            [],
        ).append(record)

    report = {}

    # --------------------------------------------------
    # Analyse each benchmark phase separately
    # --------------------------------------------------

    for phase in ("warmup", "primary", "replay"):

        path = trial / f"{phase}.json"

        # An incomplete trial may not contain all three phases.
        if not path.exists():
            continue

        stages = {}
        missing = 0

        # Read the requests recorded by our load generator.
        requests = json.loads(
            path.read_text()
        )

        for request in requests:

            request_id = request["request"]["request_id"]

            # Find the matching server-side profiling record.
            matches = profiles.get(
                request_id,
                [],
            )

            # Exclude failed or unmatched requests.
            if (
                not matches
                or request["status_code"] != 200
                or request["error"] is not None
            ):
                missing += 1
                continue

            # Replay requests reuse the original request IDs.
            # Consume each matching profile only once.
            record = matches.pop(0)

            # Copy the individual stage measurements.
            timings = dict(
                record["stages"]
            )

            # Total execution time inside the decision service.
            timings["total_service"] = (
                record["total_ms"]
            )

            # Total latency observed by the benchmark client.
            timings["client_latency"] = (
                request["latency_ms"]
            )

            # Estimate time spent outside the decision service.
            # Includes queuing, networking and framework overhead.
            timings["outside_service_estimate"] = (
                request["latency_ms"]
                - record["total_ms"]
            )

            # Group measurements by stage.
            for name, value in timings.items():

                stages.setdefault(
                    name,
                    [],
                ).append(value)

        # --------------------------------------------------
        # Calculate statistics for each stage
        # --------------------------------------------------

        report[phase] = {
            "unmatched_or_failed": missing,

            "stages": {
                name: metrics(values)
                for name, values in stages.items()
            },
        }

        print(
            f"\n{phase.upper()} | "
            f"unmatched or failed: {missing}"
        )

        # Sort stages by mean latency, highest first.
        sorted_stages = sorted(
            report[phase]["stages"].items(),
            key=lambda item: -item[1]["mean_ms"],
        )

        # Display the most expensive operations first.
        for name, result in sorted_stages:

            print(
                f"{name:28} "
                f"n={result['count']:4} "
                f"mean={result['mean_ms']:10.3f} ms "
                f"p95={result['p95_ms']:10.3f} ms"
            )

    # --------------------------------------------------
    # Save the profiling report
    # --------------------------------------------------

    output_path = trial / "profile_report.json"

    output_path.write_text(
        json.dumps(report, indent=2)
    )

    print(
        f"\nProfiling report saved to: {output_path}"
    )


if __name__ == "__main__":
    main()
