import argparse
import json
import random
import time
from pathlib import Path
from uuid import uuid4

import locust
import gevent

from gevent.event import Event
from locust.clients import HttpSession
from locust.env import Environment
from redis import Redis


# --------------------------------------------------
# Benchmark configuration
# --------------------------------------------------

# Use the isolated benchmark application and Redis instance
BASE_URL = "http://127.0.0.1:8001"
REDIS_URL = "redis://127.0.0.1:6380/0"

# Number of requests in each phase of the benchmark
PRIMARY_VIEWERS = 1000
WARMUP_VIEWERS = 150
REPLAY_REQUESTS = 50

# Each viewer requests a 30-second advertisement pod
BREAK_DURATION = 30

# Maintain consistent viewer attributes across all trials
RANDOM_SEED = 42

# Possible targeting attributes for simulated viewers
REGIONS = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune"]
DEVICES = ["MOBILE", "TV", "DESKTOP"]
TIERS = ["FREE", "PREMIUM"]

# Directory where benchmark results will be stored
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# Connect directly to Redis to independently verify allocations
redis_client = Redis.from_url(
    REDIS_URL,
    decode_responses=True,
)

# Locust environment used for HTTP request instrumentation
# We are not using Locust's conventional UI or user runner
environment = Environment()

# Viewer request generation

# Generate decision request payloads for a particular phase
def generate_requests(count, phase):

    # Use a fixed seed so viewer attributes remain reproducible
    rng = random.Random(RANDOM_SEED)

    requests = []

    for i in range(count):

        # Warm-up viewers can only access warm-up campaigns
        # Primary viewers are distributed across the five cities
        region = (
            "BENCHMARK_WARMUP"
            if phase == "warmup"
            else rng.choice(REGIONS)
        )

        # Build the payload expected by Rush's decision API
        requests.append({

            # Unique ID used by Rush for idempotency
            "request_id": str(uuid4()),

            # Each simulated viewer has a unique identity
            "viewer_id": f"{phase}_viewer_{i + 1}",

            # Randomly assign targeting attributes
            "region": region,
            "device": rng.choice(DEVICES),
            "subscription_tier": rng.choice(TIERS),

            "break_duration_seconds": BREAK_DURATION,
        })

    return requests


# --------------------------------------------------
# Individual request execution
# --------------------------------------------------

# Represents one viewer requesting an advertisement pod
def send_decision(payload, phase, client, gate, records):

    # Block this greenlet until the common gate is opened
    # This prevents viewers from sending requests prematurely
    gate.wait()

    # Record request start time using a monotonic clock
    # perf_counter is suitable for measuring elapsed time
    started_at = time.perf_counter()

    # Default values in case the request fails
    status_code = None
    response_body = None
    error = None

    try:

        # Send the viewer's decision request to Rush
        response = client.post(
            "/decisions",
            json=payload,

            # Label the request according to its benchmark phase
            name=f"decisions/{phase}",

            # Connection/read timeout
            timeout=30,
        )

        status_code = response.status_code

        # Preserve successful responses for correctness auditing
        if status_code == 200:
            response_body = response.json()

        # Preserve the error response for failed requests
        else:
            error = response.text

    # Record client/network errors without stopping other viewers
    except Exception as exc:
        error = str(exc)

    # Record when the request has finished
    completed_at = time.perf_counter()

    # Store the request, response and performance measurements
    # Every viewer contributes one record to this phase
    records.append({
        "phase": phase,
        "request": payload,
        "status_code": status_code,
        "response": response_body,
        "error": error,

        # Required for calculating overlapping requests
        "started_at": started_at,
        "completed_at": completed_at,

        # Convert elapsed time from seconds to milliseconds
        "latency_ms": (
            completed_at - started_at
        ) * 1000,
    })


# --------------------------------------------------
# Concurrent request execution
# --------------------------------------------------

# Execute all requests belonging to one benchmark phase
def run_phase(payloads, phase):

    # Shared synchronization gate for all viewers
    # The gate starts in a closed state
    gate = Event()

    # Stores the final request and response records
    records = []

    # Stores the greenlets representing individual viewers
    jobs = []

    # Stores HTTP sessions so they can be closed afterwards
    clients = []

    for payload in payloads:

        # Give each viewer an independent HTTP session
        client = HttpSession(
            base_url=BASE_URL,

            # Connect the HTTP client to Locust's request event
            request_event=environment.events.request,

            # No conventional Locust HttpUser is being used
            user=None,
        )

        clients.append(client)

        # Create a lightweight concurrent task for this viewer
        # Each greenlet calls send_decision and waits at the gate
        jobs.append(
            gevent.spawn(
                send_decision,
                payload,
                phase,
                client,
                gate,
                records,
            )
        )

    # Yield execution so greenlets can reach gate.wait()
    gevent.sleep(0)

    # Record when the simulated ad break begins
    released_at = time.perf_counter()

    # Open the gate and allow all viewers to begin sending requests
    gate.set()

    # Wait until every greenlet has finished its request
    # Propagate any unexpected greenlet errors
    gevent.joinall(jobs, raise_error=True)

    # Record when the final viewer has finished
    finished_at = time.perf_counter()

    # Release HTTP connections after the phase completes
    for client in clients:
        client.close()

    # Total time taken to complete the entire request burst
    duration = finished_at - released_at


    # --------------------------------------------------
    # Calculate actual observed concurrency
    # --------------------------------------------------

    # Record every request start and completion as an event
    events = []

    for record in records:

        # A new request increases the in-flight count
        events.append((record["started_at"], 1))

        # A completed request decreases the in-flight count
        events.append((record["completed_at"], -1))

    # Number of requests currently in progress
    in_flight = 0

    # Highest number of overlapping requests observed
    peak_in_flight = 0

    # Process request events in chronological order
    for _, change in sorted(events):

        # Add 1 for a request start or subtract 1 for completion
        in_flight += change

        # Preserve the highest observed concurrency
        peak_in_flight = max(
            peak_in_flight,
            in_flight,
        )


    # --------------------------------------------------
    # Calculate phase-level performance metrics
    # --------------------------------------------------

    # Count successful HTTP requests without client errors
    successful = sum(
        record["status_code"] == 200
        and record["error"] is None
        for record in records
    )

    summary = {
        "phase": phase,

        # Total number of completed request attempts
        "requests": len(records),

        "successful": successful,
        "failed": len(records) - successful,

        # Time between releasing the gate and finishing the burst
        "duration_seconds": duration,

        # Average throughput during this burst
        "requests_per_second": len(records) / duration,

        # Actual observed client-side concurrency
        "peak_client_in_flight": peak_in_flight,

        # Time between the first and last request starting
        # Helps us understand how synchronized the burst was
        "dispatch_window_ms": (
            max(r["started_at"] for r in records)
            - min(r["started_at"] for r in records)
        ) * 1000,
    }

    # Display a quick summary in the terminal
    print(
        f"{phase.upper()} | "
        f"Requests={len(records)} | "
        f"Successful={successful} | "
        f"Duration={duration:.3f}s | "
        f"RPS={summary['requests_per_second']:.2f} | "
        f"Peak in-flight={peak_in_flight}"
    )

    # Return individual responses and overall phase metrics
    return records, summary


# --------------------------------------------------
# Redis allocation snapshots
# --------------------------------------------------

# Capture the committed allocation count of every campaign
def snapshot_allocations(manifest):

    # Build the pacing counter key for each campaign
    # These counters are incremented by Rush's Lua allocation script
    keys = [
        f"pacing:campaign:{campaign['id']}:committed"
        for campaign in manifest
    ]

    # Fetch all counters using a single Redis round trip
    values = redis_client.mget(keys)

    # Pair each campaign ID with its committed allocation count
    # A missing counter means no allocations have been committed
    return {
        str(campaign["id"]): int(value or 0)
        for campaign, value in zip(manifest, values)
    }


# --------------------------------------------------
# Helper functions
# --------------------------------------------------

# Identify requests that received at least one commercial ad
def has_commercial_allocation(record):

    response = record["response"]

    # Ignore unsuccessful requests or missing responses
    if record["status_code"] != 200 or not response:
        return False

    # Return True if any slot contains a commercial campaign
    # Used to select meaningful requests for idempotency testing
    return any(
        slot["source"] == "COMMERCIAL"
        for slot in response["slots"]
    )


# Save benchmark data to a JSON file
def save_json(path, data):

    path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


# --------------------------------------------------
# Benchmark trial orchestration
# --------------------------------------------------

# Coordinate all three phases of a single trial
def run_trial(trial_number):

    # Give each trial its own results directory
    trial_dir = RESULTS_DIR / f"trial_{trial_number:02d}"

    # Fail if the directory exists to avoid overwriting old results
    trial_dir.mkdir(parents=True, exist_ok=False)

    # Load the campaign inventory generated by seed_campaigns.py
    manifest = json.loads(
        (RESULTS_DIR / "campaign_manifest.json").read_text()
    )

    # Preserve the exact campaign configuration used in this trial
    save_json(
        trial_dir / "campaign_manifest.json",
        manifest,
    )

    # Stores Redis allocation counts at different stages
    snapshots = {}


    # --------------------------------------------------
    # Initial state verification
    # --------------------------------------------------

    # Capture campaign allocation counts before generating traffic
    snapshots["initial"] = snapshot_allocations(manifest)

    # Every campaign must start with zero committed allocations
    # Otherwise previous traffic could contaminate our results
    if any(snapshots["initial"].values()):
        raise RuntimeError(
            "Benchmark allocation state is not clean."
        )


    # --------------------------------------------------
    # Phase 1: Warm-up
    # --------------------------------------------------

    # Generate 150 viewers targeting only warm-up campaigns
    warmup_payloads = generate_requests(
        WARMUP_VIEWERS,
        "warmup",
    )

    # Execute the warm-up burst
    # Allows workers to initialize runtime resources before primary traffic
    warmup_records, warmup_summary = run_phase(
        warmup_payloads,
        "warmup",
    )

    # Record allocations committed during warm-up
    snapshots["after_warmup"] = snapshot_allocations(manifest)

    # Preserve warm-up responses separately from primary results
    save_json(
        trial_dir / "warmup.json",
        warmup_records,
    )


    # --------------------------------------------------
    # Phase 2: Primary advertisement break
    # --------------------------------------------------

    # Generate 1000 viewers with different targeting attributes
    primary_payloads = generate_requests(
        PRIMARY_VIEWERS,
        "primary",
    )

    # Release all primary viewers as one concurrent request burst
    primary_records, primary_summary = run_phase(
        primary_payloads,
        "primary",
    )

    # Capture campaign allocations after serving primary traffic
    snapshots["after_primary"] = snapshot_allocations(manifest)

    # Preserve every primary request and its corresponding response
    save_json(
        trial_dir / "primary.json",
        primary_records,
    )

    # Save completed phases before attempting replay
    save_json(
        trial_dir / "summary.json",
        [warmup_summary, primary_summary],
    )

    # Preserve Redis snapshots even if replay fails
    save_json(
        trial_dir / "redis_snapshots.json",
        snapshots,
    )


    # --------------------------------------------------
    # Select requests for idempotency testing
    # --------------------------------------------------

    # Only select requests that received commercial advertisements
    # This allows us to check whether replay consumes more capacity
    eligible = [
        record
        for record in primary_records
        if has_commercial_allocation(record)
    ]

    # Concurrent requests may complete in a different order each trial
    # Sorting makes selection independent of completion order
    eligible.sort(
        key=lambda record: record["request"]["viewer_id"]
    )

    rng = random.Random(RANDOM_SEED)

    # Ensure there are enough successful commercial decisions to replay
    if len(eligible) < REPLAY_REQUESTS:
        reason = "Not enough successful commercial pods for replay."

        # Mark the trial as incomplete for later auditing.
        save_json(
            trial_dir / "trial_status.json",
            {
                "status": "INCOMPLETE",
                "reason": reason,
                "eligible_requests": len(eligible),
                "required_requests": REPLAY_REQUESTS,
            },
        )

        # Stop the trial without losing the completed measurements.
        raise RuntimeError(reason)

    # Randomly select 50 distinct previously processed requests
    selected = rng.sample(
        eligible,
        REPLAY_REQUESTS,
    )

    # Find the earliest request among the selected requests
    oldest_request = min(
        record["started_at"]
        for record in selected
    )

    # Rush stores committed pod results in Redis for 300 seconds
    # Leave a 30-second buffer before starting the replay phase
    
    # Ensure enough time remains before Redis expires the decisions.
    if time.perf_counter() - oldest_request >= 270:

        reason = "Insufficient time remaining for idempotency replay."

        # Mark the trial as incomplete.
        save_json(
            trial_dir / "trial_status.json",
            {
                "status": "INCOMPLETE",
                "reason": reason,
            },
        )

        # Stop without losing the completed measurements.
        raise RuntimeError(reason)

    # Extract the original request payloads without modifying them
    # Request IDs must remain identical for idempotency testing
    replay_payloads = [
        record["request"]
        for record in selected
    ]


    # --------------------------------------------------
    # Phase 3: Idempotency replay
    # --------------------------------------------------

    # Resend 50 previously processed requests
    replay_records, replay_summary = run_phase(
        replay_payloads,
        "replay",
    )

    # Ideally no campaign's committed allocation count should increase
    snapshots["after_replay"] = snapshot_allocations(manifest)

    # Preserve responses for comparison against the original decisions
    save_json(
        trial_dir / "replay.json",
        replay_records,
    )


    # --------------------------------------------------
    # Persist benchmark results
    # --------------------------------------------------

    # Save all Redis snapshots for allocation reconciliation
    save_json(
        trial_dir / "redis_snapshots.json",
        snapshots,
    )

    # Save the performance summary of all three phases
    save_json(
        trial_dir / "summary.json",
        [
            warmup_summary,
            primary_summary,
            replay_summary,
        ],
    )
    
    # Record that all three benchmark phases have completed.
    save_json(
        trial_dir / "trial_status.json",
        {
            "status": "COMPLETE",
        },
    )
    
    print(f"\nTrial {trial_number} complete.")
    print(f"Results saved to: {trial_dir}")


# --------------------------------------------------
# Script entry point
# --------------------------------------------------

if __name__ == "__main__":

    # Read command-line arguments
    parser = argparse.ArgumentParser()

    # Require a trial number to identify the results directory
    parser.add_argument(
        "--trial",
        type=int,
        required=True,
    )

    args = parser.parse_args()

    # Execute the requested benchmark trial
    run_trial(args.trial)