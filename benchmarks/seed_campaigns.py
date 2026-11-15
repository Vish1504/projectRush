# Here we will prepare the advertising inventory before we simulate viewer traffic.
#  - Hot campaigns: Eligible for every primary viewer. Designed to create competition between workers for limited campaign capacity.
#  - Cold campaigns: Eligible for smaller audience segments. Designed to exercise our inverted bitmap index, targeting logic and uneven capacity utilization.
#  - Warm-up campaigns: Exclusive to warm-up requests, allowing us to initialize application runtime resources without consuming primary campaign capacity.


import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests


# Benchmark configuration
BASE_URL = "http://127.0.0.1:8001"

# Ensures consistent campaign attributes across trials
RANDOM_SEED = 42  

# Campaign targeting dimensions
REGIONS = ["Mumbai", "Delhi", "Bengaluru", "Hyderabad", "Pune"]
DEVICES = ["MOBILE", "TV", "DESKTOP"]
TIERS = ["FREE", "PREMIUM"]
DURATIONS = [5, 10, 15, 20] # Supported campaign durations for this experiment.

# Campaigns from the same competitive group cannot share a pod
COMPETITIVE_GROUPS = [
    "SPORTSWEAR",
    "FOOD_DELIVERY",
    "AUTOMOBILE",
    "ELECTRONICS",
    "BANKING",
    "TELECOM",
]

# __file__ represents the path of this Python script.
#
# resolve() converts it into an absolute filesystem path.
# parent gives us the directory containing this script.
#
# Therefore, this points to:
# projectRush/benchmarks/results/
#
# This works regardless of the directory from which
# we execute the script.
RESULTS_DIR = Path(__file__).resolve().parent / "results"


# To generate synthetic campaigns
def generate_campaigns():
    # Generate 100 primary campaigns and 5 warm-up campaigns.
    # This function will only generate Python dictionaries and does not communicate with Rush, PostgreSQL or Redis.

    rng = random.Random(RANDOM_SEED)
    now = datetime.now(timezone.utc) #Capture the current time in UTC.

    campaigns = []

    # Build a campaign payload matching our CampaignCreate schema
    def build_campaign(
        name, capacity, regions, devices,
        tiers, duration, group
    ):
        
        # Construct a dictionary matching Rush's CampaignCreate pydantic schema.

        # This helper avoids duplicating the same dictionary structure for every category of campaign.
        
        return {
            "name": name,
            "capacity": capacity,

            # Keep campaigns within their serving window
            # throughout the benchmark
            "start_time": (now - timedelta(hours=1)).isoformat(),
            "end_time": (now + timedelta(hours=6)).isoformat(),

            "regions": regions,
            "devices": devices,
            "subscription_tiers": tiers,
            "frequency_cap_per_hour": rng.randint(1, 5),
            "duration_seconds": duration,
            "competitive_group": group,
        }

    # Now we generate the 15 Hot Campaigns
    
    # Hot campaigns target every region, device and subscription tier included in our simulated audience.
    
    # This workload is intended to exercise:
    #   - Hierarchical capacity leasing
    #   - Worker-local allocation pools
    #   - Durable lease refills
    #   - Concurrent capacity enforcement
    
    for i in range(15):
        campaigns.append(
            build_campaign(
                # # Produces names such as HOT_001, HOT_002, and HOT_015.
                name=f"HOT_{i + 1:03d}",
                # Random capacity bw 150 and 300
                capacity=rng.randint(150, 300),
                regions=REGIONS.copy(), # Include every benchmark region.
                devices=DEVICES.copy(), # Target all available device types.
                tiers=TIERS.copy(), # Target both Free and Premium viewers.
                duration=rng.choice(DURATIONS),  # Select one advertisement duration randomly
                group=rng.choice(COMPETITIVE_GROUPS),
            )
        )

    # # Now we generate the 85 long-tail campaigns:
    
    # Long-tail campaigns target smaller, more specific sections of our simulated audience.
    
    # Unlike hot campaigns, these campaigns are not eligible for every viewer.
    
    # This allows us to exercise:
    #   - Inverted bitmap index filtering
    #   - Targeting accuracy
    #   - Heterogeneous campaign selection
    #   - Capacity utilization under uneven demand
    for i in range(85):
        campaigns.append(
            build_campaign(
                # Produces TAIL_001 through TAIL_085.
                name=f"TAIL_{i + 1:03d}",
                capacity=rng.randint(50, 500),
                # Select either one or two distinct regions.
                regions=rng.sample(REGIONS, rng.randint(1, 2)),
                # Select either one or two distinct device types.
                devices=rng.sample(DEVICES, rng.randint(1, 2)),
                # Select exactly one subscription tier.
                tiers=rng.sample(TIERS, 1),
                # Randomly select an advertisement duration.
                duration=rng.choice(DURATIONS),
                 # Adding None creates another possible choice
                group=rng.choice(COMPETITIVE_GROUPS + [None]),
            )
        )

    # Now we generate 5 warm-up campaigns:
    
    # Before the main benchmark, we will send 150 requests
    # to exercise newly started application workers.
    #
    # These requests help initialize database connections,
    # connection pools and relevant runtime state.
    #
    # However, we do not want them to consume capacity from
    # the 100 campaigns used in our primary benchmark.
    #
    # Therefore, we introduce a fictional geographical region
    # called BENCHMARK_WARMUP.
    #
    # Only warm-up viewers will use this region.
    # Normal benchmark viewers will use our five actual cities.
    #
    # This provides targeting-based separation between warm-up
    # inventory and primary benchmark inventory.
    #
    # Warm-up measurements will still be recorded and reported
    # separately rather than discarded.
    for i in range(5):
        campaigns.append(
            build_campaign(
                name=f"WARMUP_{i + 1:03d}",
                capacity=500,

                # Dedicated fictional region that normal benchmark viewers will never request.
                regions=["BENCHMARK_WARMUP"],
                
                # Allow warm-up requests from any device type.
                devices=DEVICES.copy(),
                
                # Allow both subscription tiers.
                tiers=TIERS.copy(),

                # Continue exercising advertisement
                # pod construction during warm-up.
                duration=rng.choice(DURATIONS),

                # No competitive restriction for these
                # dedicated warm-up campaigns.
                group=None,
            )
        )

    # Return the complete list containing:
    # 15 hot + 85 long-tail + 5 warm-up = 105 campaigns.
    return campaigns


# Now we CREATE AND ACTIVATE CAMPAIGNS
def seed_campaigns():
    """Create, activate and record synthetic campaigns for the benchmark."""


    # First we ensure that benchmarks/results/ exists.
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    
    # The manifest will contain the original campaign payloads along with their PostgreSQL-generated campaign IDs.
    manifest = []

    # Reuse HTTP connections across campaign API requests
    with requests.Session() as session:
        # VERIFY THAT THE BENCHMARK IS CLEAN
        
        # Retrieve all existing campaigns from Rush.
        #
        # This is a safety check to prevent accidentally
        # appending another 105 campaigns to an existing
        # benchmark dataset.
        existing = session.get(
            f"{BASE_URL}/campaigns",
            timeout=10,
        )
        existing.raise_for_status()

        if existing.json():
            # Raise an exception if the API returns an unsuccessful
        # HTTP status such as 404 or 500.
            raise RuntimeError(
                "Benchmark database is not empty. "
                "Reset it before seeding."
            )
            
            # GENERATE THE WORKLOAD
        campaigns = generate_campaigns()

        # Iterate over the generated campaign dictionaries,
        # creating and activating one campaign at a time.
        #
        # We use the existing API instead of directly inserting
        # records into PostgreSQL.
        
        for payload in campaigns:

             # Send the campaign payload to our existing
            # POST /campaigns endpoint.
            #
            # json=payload automatically serializes the
            # dictionary into a JSON request body.
            #
            # timeout=10 prevents indefinite waiting for
            # a connection or response data.
            response = session.post(
                f"{BASE_URL}/campaigns",
                json=payload,
                timeout=10,
            )
            response.raise_for_status()

            campaign = response.json()
            campaign_id = campaign["id"]

            # ACTIVATE THE CAMPAIGN
            # Creating a campaign does not automatically make
            # it eligible for advertisement decisions.
            #
            # Rush initially creates campaigns in DRAFT status.
            #
            # We therefore call the existing activation endpoint
            # to transition the campaign to ACTIVE.
            #
            # Activation also:
            #   1. Initializes remaining_unleased_capacity.
            #   2. Commits the updated state to PostgreSQL.
            #   3. Increments rush:index:version in Redis.
            #
            # Workers can then detect the configuration change
            # and refresh their local inverted indexes.
            activation = session.post(
                f"{BASE_URL}/campaigns/{campaign_id}/activate",
                timeout=10,
            )

            # Stop if the activation request failed.
            activation.raise_for_status()

            # An HTTP success status alone does not guarantee
            # that the campaign reached the expected business
            # state.
            #
            # Explicitly verify that it is now ACTIVE.
            if activation.json()["status"] != "ACTIVE":
                raise RuntimeError(
                    f"Campaign {campaign_id} is not ACTIVE"
                )

            # Record the campaign in our manifest
            
            # **payload unpacks every key-value pair from
            # the original campaign dictionary.
            #
            # We then add:
            #   - The database-generated campaign ID.
            #   - The verified ACTIVE status.
            #
            # This gives our auditor a complete reference
            # containing the original campaign configuration.
            #
            # We retain the original payload because the
            # CampaignResponse schema does not return every
            # targeting attribute.
            manifest.append({
                **payload,
                "id": campaign_id,
                "status": "ACTIVE",
            })


            # Print progress so we can observe campaign
            # creation and diagnose where execution stops
            # if an error occurs.
            print(
                f"Activated {payload['name']} | "
                f"ID={campaign_id} | "
                f"Capacity={payload['capacity']} | "
                f"Duration={payload['duration_seconds']}s"
            )


   
    # Construct the destination file path.
    #
    # Final location:
    # benchmarks/results/campaign_manifest.json
    manifest_path = RESULTS_DIR / "campaign_manifest.json"

    # json.dumps() converts our list of Python dictionaries
    # into a JSON-formatted string.
    #
    # indent=2 makes the JSON human-readable.
    #
    # write_text() writes that string into the destination file.
    #
    # If the file already exists, its contents are overwritten.
    #
    # We save the manifest only after successfully creating
    # and activating every campaign.
    manifest_path.write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    print("\nCampaign seeding complete.")

    # Should report 105 if every campaign was successfully created, activated and recorded.
    print(f"Total campaigns: {len(manifest)}")

    # Show the absolute location of the generated manifest.
    print(f"Manifest saved to: {manifest_path}")


if __name__ == "__main__":
    seed_campaigns()
