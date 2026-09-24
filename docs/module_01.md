# Rush Academy — Module 1: Product & Domain Model

## Objective

By the end of this module, you should understand:

- what Rush does;
- who interacts with it;
- what entities exist;
- how a campaign moves through the system;
- what makes an ad eligible;
- how capacity is counted;
- what Rush must never allow to happen.

No FastAPI, Redis, PostgreSQL, or Kafka yet.

---

## Q1. What exactly is Project Rush?

Project Rush is a low-latency ad-decisioning backend for high-concurrency streaming workloads.

When a streaming platform encounters an ad opportunity for a viewer, it sends Rush the viewer/context information and the available ad-break duration.

Rush determines which eligible advertisements should be allocated to that opportunity and returns an ordered ad pod.

### Direct caller

The direct user of Rush is the streaming/ad-platform backend.

The viewer does not interact with Rush directly.

Advertisers or agencies configure campaigns beforehand rather than calling the decision engine for individual viewers.

### Example input

```text
request_id
viewer_id
region
device
subscription_tier
content_category
break_duration
```

### Example output

```text
30-second pod

Nike       15s
Samsung    10s
Swiggy      5s
```

### Core problem

Rush must coordinate limited campaign inventory under synchronized concurrent demand while respecting hard business constraints and maintaining low decision latency.

Its job is to decide **which ads** should fill an opportunity.

Rush does **not** decide where the ad break occurs and does not perform the actual video delivery or ad insertion.

---

## Q2. What are the core domain entities?

### Advertiser

The organization paying to promote a product, service, or brand.

Examples:

- Nike
- Samsung
- Haldiram's
- Manyavar

An agency may manage campaigns on behalf of an advertiser later, but V1.1 does not require a separate agency model.

### Campaign

A configured advertising delivery objective.

A campaign owns concepts such as:

- delivery capacity;
- start/end time;
- targeting;
- frequency policy;
- priority;
- pacing;
- lifecycle status;
- competitive category or exclusion rules.

Example:

```text
Nike Diwali 2026
Capacity: 500,000 allocations
Regions: Mumbai, Delhi
Start: Oct 10
End: Oct 25
Frequency cap: 3/day
```

A campaign is **not** the advertisement itself.

### Creative

The actual advertising asset associated with a campaign.

A single campaign may contain multiple creatives:

```text
30-second brand film
15-second cut
10-second product ad
5-second bumper
```

The campaign owns the delivery objective.

The creative owns the media asset and its properties, such as duration.

### Campaign-to-Creative Relationship

A Campaign owns one or more Creatives.

```text
Campaign
   |
   +-- Creative A
   +-- Creative B
   +-- Creative C
```

Candidate discovery operates primarily at the **campaign level**:

1. find campaigns eligible for the current opportunity;
2. reject campaigns that fail hard constraints;
3. examine the creatives belonging to surviving campaigns;
4. select specific creatives during pod construction.

The final Ad Pod contains **Creatives**, not Campaign objects.

Campaign-level state controls delivery eligibility and capacity, while Creative-level properties such as duration determine how a specific asset can participate in a pod.

### Viewer

The person/account/device for whom Rush is making a decision.

Relevant V1.1 attributes may include:

```text
viewer_id
region
device
subscription_tier
content_category
```

V1.1 deliberately avoids unnecessary demographic complexity.

### Ad Opportunity

A specific request to fill a certain amount of advertising time.

Example:

> Viewer X currently has a 30-second ad opportunity.

Rush does not decide where the break occurs. The streaming platform tells Rush that an opportunity exists.

### Ad Pod

The ordered group of creatives Rush returns for one ad opportunity.

Example:

```text
30-second pod

Creative A    15s
Creative B    10s
Creative C     5s
```

The ad pod is essentially Rush's main decision output.

---

## Q3. What is the campaign lifecycle?

V1.1 uses the following lifecycle:

```text
DRAFT
  ↓
SCHEDULED
  ↓
ACTIVE ↔ PAUSED
  ↓
COMPLETED
```

`CANCELLED` is a terminal state available from a non-terminal campaign.

### DRAFT

The campaign exists but is still being configured.

It cannot participate in decisions.

### SCHEDULED

The campaign configuration is valid, but its start time has not yet arrived.

### ACTIVE

The campaign is allowed to participate in decisioning.

`ACTIVE` does not guarantee selection. All request-specific constraints must still pass.

### PAUSED

Campaign delivery is temporarily disabled but may later resume.

```text
ACTIVE → PAUSED → ACTIVE
```

### COMPLETED

The campaign has finished normally.

Possible completion reasons include:

```text
END_TIME_REACHED
CAPACITY_EXHAUSTED
```

Rather than creating many separate terminal states, Rush can keep a single `COMPLETED` state with a completion reason.

### CANCELLED

The campaign has been permanently terminated before normal completion.

### Core lifecycle rule

> Only `ACTIVE` campaigns may enter the decision pipeline.

A campaign must also be within its valid time window at decision time.

---

## Q4. What makes a campaign eligible?

Rush separates **hard constraints** from **soft preferences**.

### Hard constraints

If a hard constraint fails, the campaign is invalid for the current request.

Examples:

```text
campaign must be ACTIVE
request must fall within campaign time window
configured region must match
configured device must match
configured subscription tier must match
configured content category must match
frequency cap must not be exceeded
campaign capacity must remain
creative must fit the available pod duration
competitive exclusions must be satisfied
```

Failure does **not** permanently reject the campaign.

It only means:

> Not eligible for this ad opportunity.

### Soft preferences

Soft preferences rank campaigns that have already passed every hard constraint.

Examples:

```text
priority
pacing
business value
delivery urgency
```

Conceptually:

```text
ACTIVE CAMPAIGNS
        ↓
HARD CONSTRAINT FILTER
        ↓
FEASIBLE CAMPAIGNS
        ↓
SOFT SCORING
        ↓
POD CONSTRUCTION
```

A soft preference can never override a hard constraint.

### Pod filling rule

Rush should try to use the available duration efficiently, but perfect fill is not a correctness requirement.

The hard rule is:

```text
sum(creative durations) <= opportunity duration
```

An under-filled or even empty pod is valid if no valid combination exists.

Rush must never force invalid creatives into a pod just to fill time.

### No-Fill Behavior

If no campaign/creative combination satisfies all hard constraints, Rush may return a valid empty pod.

Example:

```json
{
  "request_id": "req_123",
  "status": "NO_FILL",
  "ads": []
}
```

This is still a successful decision outcome and may be returned with `200 OK`.

Rush must never violate targeting, capacity, frequency, duration, or competitive-exclusion constraints merely to avoid returning an empty pod.

---

## Q5. What does campaign capacity mean?

Selection and confirmed delivery are different events.

A viewer may receive an ad decision and then close the app before the ad actually plays.

However, V1.1 deliberately uses simplified **decision-level allocation accounting**.

### V1.1: committed allocation

Once Rush successfully issues an ad decision containing a campaign, the corresponding campaign capacity is permanently consumed.

Example:

```text
Capacity before decision: 500,000
Rush successfully allocates Nike
Capacity after decision:  499,999
```

If the viewer later leaves before seeing the ad, V1.1 does not reclaim that capacity.

Rush knowingly accepts:

> **under-delivery / wasted inventory**

This deliberately prioritizes simple hard-cap correctness over perfect inventory utilization.

Public terminology should use:

- **committed allocation**
- **decision-level allocation**

rather than calling it a confirmed impression.

### Planned V1.2

V1.2 will introduce temporary reservations and asynchronous confirmation/reconciliation:

```text
AVAILABLE
    ↓
RESERVED
   / \
  /   \
CONFIRMED
     EXPIRED
        ↓
     RECLAIM
```

Kafka will be introduced in V1.2 for impression confirmation, reconciliation, duplicate handling, and related asynchronous workflows.

The reservation timeout should be configurable rather than hard-coded, because playback delay, pod position, and network latency may vary.

---

## Q6. What is the complete Rush workflow?

The three major external actors are:

```text
Advertiser / Agency
Streaming Platform
Viewer
```

Rush sits between campaign configuration and real-time streaming decisions.

### End-to-end flow

```text
Advertiser creates campaign
        ↓
Campaign configuration added
        ↓
Creatives registered
        ↓
Campaign validated
        ↓
DRAFT → SCHEDULED
        ↓
Start time arrives
        ↓
ACTIVE
        ↓
Viewer reaches an ad opportunity
        ↓
Streaming platform calls Rush
        ↓
Rush discovers candidate campaigns
        ↓
Hard constraints applied
        ↓
Valid campaigns ranked
        ↓
Rush constructs a valid ad pod
        ↓
Pod returned to streaming platform
        ↓
Streaming platform handles playback
        ↓
V1.1 allocation capacity remains permanently consumed
```

### Example request

```text
viewer_id = user_9481
region = Mumbai
device = Android TV
subscription = Premium
content_category = Sports
break_duration = 30 seconds
request_id = req_88204
```

### Candidate discovery

Rush identifies campaigns that could possibly apply to the request.

Later, Module 6 replaces naive campaign scans with inverted bitmap indexes.

### Hard filtering

Rush checks:

```text
campaign status
time window
targeting
frequency
capacity
creative duration
competitive rules
```

### Ranking

Among valid candidates, Rush evaluates soft preferences such as:

```text
priority
pacing
business value
```

### Pod construction

Rush finds the best valid combination for the available duration.

Example:

```text
30-second break

Nike       15s
Samsung    10s
Swiggy      5s
----------------
Total      30s
```

Competitive exclusions may make some combinations invalid even if each campaign is individually eligible.

### Output

Rush returns the selected creatives.

The streaming platform handles the actual video insertion/playback.

---

## Q7. What must Rush never allow to happen?

Rush must preserve explicit system invariants.

### 1. Lifecycle validity

Only `ACTIVE` campaigns may participate in ad decisions.

### 2. Time-window validity

A campaign must never be selected before its configured start time or after its configured end time.

### 3. Targeting validity

Every selected campaign must satisfy all configured targeting constraints for the request.

### 4. Frequency-cap validity

A viewer must not receive a campaign beyond its configured frequency policy.

### 5. Capacity safety

Rush must never issue more committed allocations than the campaign's configured allocatable capacity.

### 6. Pod-duration safety

The returned pod must never exceed the available opportunity duration.

```text
30-second break

15 + 10 + 5 = valid
20 + 15     = invalid
```

An under-filled pod is valid.

If no eligible campaign/creative combination exists, Rush may return an empty pod with:

```json
{
  "status": "NO_FILL",
  "ads": []
}
```

This is a valid successful decision result. Rush must never violate a hard constraint merely to fill the opportunity.

### 7. Competitive exclusion

If two campaigns are configured as mutually exclusive competitors, they must not coexist in the same returned pod.

### 8. Creative validity

Every selected creative must:

```text
belong to an eligible campaign
be valid for the current opportunity
fit within the pod duration constraint
```

### 9. V1.1 allocation accounting

Once Rush successfully issues an allocation, that campaign capacity is permanently consumed.

Playback failure does not reclaim capacity in V1.1.

This is an intentional tradeoff.

### 10. Retry safety / idempotency

Every decision request must contain a deterministic `request_id` or equivalent idempotency key generated by the caller.

Within the configured idempotency retention window:

- repeated identical requests must return the same decision;
- concurrent duplicates must not create multiple allocations;
- campaign capacity must be consumed at most once;
- the original decision payload must remain stable;
- reusing the same request ID with a materially different payload is invalid.

### Global rule

> Hard constraints always dominate soft optimization.

Pacing, priority, or business value may only influence selection among campaigns that are already valid.

---

## Domain Model

```text
Advertiser
    |
    +---- Campaign
             |
             +---- Creative(s)
             |
             +---- Targeting Rules
             |
             +---- Frequency Policy
             |
             +---- Capacity
             |
             +---- Lifecycle State


Viewer + Ad Opportunity
          |
          v
         Rush
          |
          v
        Ad Pod
          |
          +---- Creative
          +---- Creative
          +---- Creative
```

---

## Module 1 Outcome

At the end of Module 1, Rush has a defined:

- product boundary;
- direct system caller;
- input/output contract conceptually;
- domain vocabulary;
- campaign lifecycle;
- eligibility model;
- hard/soft constraint separation;
- V1.1 allocation-accounting model;
- V1.2 reconciliation direction;
- end-to-end workflow;
- correctness contract;
- idempotency expectations.

Implementation begins in Module 2.

---

## References

For campaign lifecycle/state modeling:

- Google Ad Manager — *Review the status of a line item*
- Google Ads — *About campaign statuses*

For the broader ad-decisioning domain and live-event flow:

- JioHotstar Engineering — *Journey of an Ad Request: The Hidden Engineering*
- JioHotstar Engineering — *Journey of an Ad Request: The Hidden Engineering*
