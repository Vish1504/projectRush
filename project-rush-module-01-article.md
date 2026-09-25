# Project Rush, Part 1: Designing the Decision Brain Behind a Live Ad Break

*Project Rush is a working title for a real-time ad allocation backend built around one question: what happens when a huge number of viewers need an ad decision at almost the same moment?*

Imagine a live cricket final.

A hundred thousand viewers are watching the same stream. The innings ends, the broadcast enters a 30-second ad break, and suddenly thousands of devices need advertisements.

At first glance, the problem sounds almost trivial.

There are ads. There are viewers. Pick a few ads and send them back.

But then the rules begin to arrive.

Nike has only a limited number of allocations remaining.

A viewer has already seen the same campaign three times today.

One campaign is only allowed to run in Mumbai.

Another campaign targets Android TV users.

A 15-second creative cannot fit into the final 10 seconds of a pod.

Two competing brands may not be allowed to appear in the same break.

Some campaigns are behind schedule and need to be delivered more aggressively.

And all of these decisions are happening while a large number of requests arrive concurrently.

That is the problem Project Rush is built around.

Rush is not an ad player, a video streaming system, or a full advertising exchange.

It is the **decision brain** in the middle.

Its job is to answer one question quickly and correctly:

> **Given this viewer, this context, this ad opportunity, and the current state of every campaign, which creatives should be returned?**

Module 1 was about defining that problem precisely before writing a single line of backend code.

---

## Why build Rush?

I wanted this project to be very different from my storage-engine work.

A storage engine pulls you downward into files, memory, persistence, binary formats, recovery, compaction, and low-level correctness.

Rush pushes in the other direction.

It is about distributed state, APIs, concurrency, shared capacity, hot paths, data structures, optimization, failure semantics, and production-style backend engineering.

The advertising domain is useful because the constraints are real and concrete.

Campaigns have budgets.

Viewers have frequency limits.

Inventory is finite.

Decisions are latency-sensitive.

Traffic can arrive in synchronized bursts.

And incorrect decisions have consequences.

If Rush allocates beyond a campaign's configured capacity, that is a correctness failure.

If it serves a campaign to an invalid viewer, that is a correctness failure.

If a retry spends the same campaign capacity twice, that is a correctness failure.

So the project is not really about advertisements.

It is about this broader systems problem:

> **How do we allocate scarce shared resources correctly under high concurrency while still making useful decisions quickly?**

Advertising simply gives that problem a realistic shape.

---

# The seven questions of Module 1

Before designing APIs, databases, Redis keys, leases, or algorithms, I wanted to answer seven questions.

They became the foundation of Rush.

---

## 1. What exactly is Rush?

Suppose a streaming platform knows that a viewer has reached a 30-second advertising opportunity.

The streaming backend might know:

```text
request_id
viewer_id
region
device
subscription_tier
content_category
break_duration
```

It sends that information to Rush.

Rush looks at the campaigns currently available to the platform, checks which ones are valid for this viewer and this opportunity, chooses specific creatives, constructs an ordered pod, and returns something conceptually like:

```text
30-second pod

Nike       15s
Samsung    10s
Swiggy      5s
```

The important boundary is this:

**Rush decides what should be shown.**

It does not decide where the advertisement break occurs.

It does not transcode video.

It does not splice advertisements into a stream.

It does not deliver the media bytes to the viewer.

The streaming platform remains responsible for those things.

That boundary matters because it keeps Rush focused.

Its core responsibility is **real-time decisioning and allocation**.

The direct caller is therefore not the viewer and not the advertiser.

It is another backend service inside a streaming or advertising platform.

---

## 2. What are the core entities?

Once the boundary was clear, the next job was defining the vocabulary of the system.

### Advertiser

The advertiser is the organization paying to promote something.

Nike, Samsung, Haldiram's, Manyavar — the specific brand does not matter.

For V1.1, I do not need a separate agency model. An agency can conceptually manage campaigns on behalf of an advertiser without becoming a first-class entity yet.

### Campaign

A campaign represents a **delivery objective**.

For example:

```text
Nike Diwali 2026

Capacity: 500,000 allocations
Regions: Mumbai, Delhi
Start: Oct 10
End: Oct 25
Frequency cap: 3/day
```

A campaign can own:

- delivery capacity;
- start and end times;
- targeting rules;
- frequency policy;
- priority;
- pacing information;
- lifecycle state;
- competitive-exclusion rules.

The campaign is not the video itself.

That distinction becomes important later.

### Creative

The creative is the actual advertisement asset.

One campaign may have multiple creatives:

```text
30-second brand film
15-second cut
10-second product ad
5-second bumper
```

The **campaign owns the delivery objective**.

The **creative owns the asset and properties such as duration**.

That gives Rush a useful hierarchy:

```text
Campaign
   |
   +-- Creative A
   +-- Creative B
   +-- Creative C
```

Candidate discovery happens primarily at the campaign level.

The pod builder ultimately selects **specific creatives**.

That means a campaign can be perfectly eligible even when one of its creatives cannot fit the remaining duration.

For example:

```text
10 seconds remain in the pod

30s creative -> unusable
15s creative -> unusable
10s creative -> valid
 5s creative -> valid
```

### Viewer

The viewer is the person, account, or device for whom Rush is making the decision.

For V1.1 I only need synthetic, operationally relevant attributes such as:

```text
viewer_id
region
device
subscription_tier
content_category
```

There is no need to introduce sensitive demographic targeting just to make the project look complicated.

It would add privacy and fairness concerns without improving the systems problem I actually want to study.

### Ad Opportunity

An ad opportunity means:

> The streaming platform has some amount of advertising time available right now.

For example:

```text
Viewer X has a 30-second ad opportunity.
```

Again, Rush does not discover that opportunity.

The caller tells Rush that it exists.

### Ad Pod

The pod is the ordered output:

```text
Creative A    15s
Creative B    10s
Creative C     5s
```

This is the thing Rush is ultimately building.

---

## 3. How does a campaign move through the system?

Campaigns need a lifecycle.

Without one, "active campaign" becomes ambiguous.

The V1.1 lifecycle is intentionally small:

```text
DRAFT
  ↓
SCHEDULED
  ↓
ACTIVE ↔ PAUSED
  ↓
COMPLETED
```

There is also a terminal `CANCELLED` state.

### DRAFT

The campaign is still being configured.

It cannot participate in decisions.

### SCHEDULED

The configuration is valid, but the campaign's start time has not arrived.

### ACTIVE

The campaign is allowed to compete for ad decisions.

This does **not** mean it will be selected.

It still has to pass every request-specific rule.

### PAUSED

The campaign is temporarily disabled.

It may later return to `ACTIVE`.

### COMPLETED

The campaign finished normally.

For example:

```text
END_TIME_REACHED
CAPACITY_EXHAUSTED
```

Instead of creating a separate lifecycle state for every possible reason, Rush can keep `COMPLETED` as the state and record a completion reason separately.

### CANCELLED

The campaign has been permanently terminated before normal completion.

The first major lifecycle invariant is simple:

> **Only ACTIVE campaigns may enter the decision pipeline.**

And even an active campaign must still satisfy:

```text
start_time <= decision_time < end_time
```

This distinction between lifecycle state and temporal validity will matter when the system becomes concurrent.

---

## 4. What actually makes a campaign eligible?

This question produced one of the most important architectural ideas in Rush:

> **Hard constraints and soft preferences must be separated.**

A campaign first has to be valid.

Only then is Rush allowed to ask whether it is desirable.

### Hard constraints

Hard constraints are rules that cannot be violated.

Examples:

```text
campaign must be ACTIVE
request must be inside the campaign time window
region must match
device must match
subscription tier must match
content category must match
frequency cap must not be exceeded
campaign capacity must remain
a usable creative must fit
competitive exclusions must be respected
```

If one of these fails, the campaign is simply not eligible for this opportunity.

That does not mean the campaign is broken.

It might be valid for the next viewer.

### Soft preferences

Once Rush has a set of valid candidates, it can rank them using signals such as:

```text
priority
pacing
business value
delivery urgency
```

So the conceptual pipeline becomes:

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

That ordering is deliberate.

A very high-priority campaign cannot bypass its frequency cap.

A campaign that is badly behind pace cannot exceed its capacity.

A lucrative campaign cannot ignore targeting.

**Optimization happens inside the feasible set.**

It never redefines feasibility.

### What if nothing is valid?

Then Rush should return nothing.

That sounds obvious, but defining it now prevents a dangerous temptation later: bending the rules to avoid an empty result.

A successful decision can therefore be:

```json
{
  "request_id": "req_123",
  "status": "NO_FILL",
  "ads": []
}
```

This can still be a `200 OK`.

The request was valid.

Rush processed it successfully.

There simply was no valid inventory.

That distinction will shape the API contract later.

---

## 5. What does campaign capacity actually mean?

This was one of the most important design choices in Module 1.

Imagine Rush allocates an advertisement to a viewer.

Then, before playback begins, the viewer closes the app.

Did the campaign really deliver an impression?

No.

Should Rush immediately reclaim that capacity?

For V1.1, also no.

That is intentional.

### V1.1: decision-level allocation accounting

In V1.1, once Rush successfully commits and returns a decision containing a campaign, that unit of campaign capacity is permanently consumed.

Example:

```text
Capacity before decision: 500,000

Rush returns a Nike allocation

Capacity after decision: 499,999
```

If playback later fails, V1.1 does not reclaim the allocation.

The right term is therefore not necessarily "confirmed impression."

I use:

- **committed allocation**
- **decision-level allocation**

This design knowingly accepts:

> **under-delivery / wasted inventory**

Why accept that?

Because V1.1 is trying to prove something more fundamental first:

> Can Rush preserve hard allocation limits under concurrency?

Trying to solve confirmed playback, reservation expiry, delayed events, duplicate impression beacons, and safe reclamation immediately would pull an asynchronous event-processing problem into the first version.

That belongs in V1.2.

### V1.2: reservations and reconciliation

The next version will evolve the model toward something like:

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

A decision creates a temporary reservation.

Playback confirmation converts it into a confirmed allocation.

Unused reservations may eventually be reclaimed where it is safe to do so.

This requires much more careful semantics.

A reservation timeout cannot simply be "30 seconds."

An advertisement may appear late in a pod.

Playback can be delayed.

Networks can be slow.

Events can arrive late or more than once.

That is why the reservation timeout will be configurable and why V1.2 introduces asynchronous reconciliation rather than pretending those edge cases do not exist.

---

## 6. What is the complete user workflow?

With the domain model in place, the whole system becomes easier to see.

There are three broad external actors:

```text
Advertiser / Agency
Streaming Platform
Viewer
```

The flow is:

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
```

In V1.1, the corresponding committed allocations remain consumed once the decision succeeds.

A request might look conceptually like:

```text
request_id = req_88204
viewer_id = user_9481
region = Mumbai
device = Android TV
subscription = Premium
content_category = Sports
break_duration = 30 seconds
```

Early versions of Rush can discover candidates using straightforward scans.

Later, candidate discovery becomes its own systems problem.

If Rush eventually stores thousands of campaigns, checking each one on every request is wasteful.

The plan is to move toward **inverted bitmap indexes**.

Conceptually:

```text
Mumbai campaigns
AND
Android TV campaigns
AND
Sports campaigns
AND
Premium campaigns
```

The intersection gives a much smaller candidate set before deeper checks begin.

Importantly, that does not magically make filtering O(1).

Bitmap operations still have work proportional to their representation.

The advantage is that set intersections can be compact, cache-friendly, and dramatically cheaper than repeatedly evaluating every campaign one by one.

---

## 7. What must Rush never allow to happen?

This final question turned the project from a feature list into a system with a correctness contract.

I wrote the rules down as explicit invariants.

### Invariant 1: Lifecycle eligibility

Every selected campaign must be `ACTIVE`.

### Invariant 2: Temporal eligibility

Every selected campaign must satisfy:

```text
start_time <= decision_time < end_time
```

### Invariant 3: Targeting validity

Every selected campaign must satisfy all configured hard targeting constraints.

### Invariant 4: Frequency-cap safety

A viewer must not receive a campaign beyond its configured frequency policy.

### Invariant 5: Hard capacity bound

For every campaign:

```text
CommittedAllocations(campaign)
    <=
ConfiguredCapacity(campaign)
```

This must remain true even when many decision requests arrive concurrently.

### Invariant 6: Pod-duration safety

For every returned pod:

```text
sum(creative durations) <= opportunity duration
```

Under-fill is valid.

No-fill is valid.

Violating a hard constraint to fill the last few seconds is not valid.

### Invariant 7: Competitive exclusion

If two campaigns are explicitly configured as conflicting:

```text
Conflicts(campaign_a, campaign_b) == true
```

they must not coexist in the same pod.

This is intentionally not implemented as:

```text
Brand(A) != Brand(B)
```

Two different brands are not automatically competitors, and two campaigns from the same advertiser are not automatically invalid together.

The relationship should be configured explicitly.

### Invariant 8: Creative validity

Every selected creative must belong to an eligible campaign and must itself be valid for the opportunity.

The pod contains creatives.

Campaigns provide the state and constraints behind those creatives.

### Invariant 9: V1.1 allocation accounting

Once an allocation has been successfully committed and returned, its capacity remains consumed.

Playback failure does not reclaim it in V1.1.

### Invariant 10: Idempotent allocation

Retries must not spend capacity again.

Every decision request therefore carries a deterministic `request_id` or equivalent idempotency key.

Within a configured retention window:

```text
same request_id + same request
→ same decision
→ no additional capacity consumption
```

Concurrent duplicate requests must also consume capacity at most once.

And:

```text
same request_id + materially different request
→ invalid
```

That last rule matters.

An idempotency key identifies a logical operation.

It cannot safely mean two different things.

### Invariant 11: Hard constraints dominate optimization

This is the umbrella rule:

> **Soft signals may rank valid choices. They may never legalize an invalid choice.**

That sentence will influence almost every later module.

---

# Where Rush goes from here

Module 1 deliberately contains almost no implementation.

The idea was to freeze the semantics before choosing mechanisms.

Now the interesting part begins.

---

## V1.1: the flagship version

V1.1 is the first version I want to consider genuinely portfolio-ready.

The goal is not merely to expose a `/decision` endpoint.

The goal is to build a backend whose architecture has a defensible reason for existing.

The planned V1.1 includes:

### A real backend service

Rush will use a Python backend stack built around:

```text
Python
FastAPI
Pydantic
PostgreSQL
SQLAlchemy
Alembic
Redis
```

The project will also include:

- validation;
- structured errors;
- migrations;
- health checks;
- structured logging;
- configuration;
- integration tests;
- load tests;
- deployment documentation;
- Docker-based local deployment.

No frontend is required.

The product is the backend.

### Correct centralized allocation first

Before attempting anything clever, Rush needs a correct baseline.

The first allocator should atomically enforce capacity through centralized shared state.

That gives the project a useful progression:

```text
naive read/check/write
        ↓
correct centralized atomic allocation
        ↓
hierarchical leased allocation
```

The naive version is useful because it demonstrates the concurrency bug.

The centralized version demonstrates correctness.

The leased version attempts to preserve correctness while reducing global coordination.

### Frequency caps

Frequency state has a different access pattern from global campaign capacity.

A campaign may be globally hot.

Viewer/campaign frequency keys are naturally spread across many viewers.

Redis is a natural place to explore those semantics.

### Candidate indexing

Instead of scanning every campaign for every decision, V1.1 will build inverted bitmap indexes for common targeting dimensions.

The purpose is not to claim impossible constant-time filtering.

The purpose is to reduce unnecessary candidate work using efficient set intersections.

### Hierarchical capacity leases

This is one of the central systems ideas in Rush.

Imagine a campaign has:

```text
10,000 remaining allocations
```

Instead of forcing every worker to coordinate with one global counter for every viewer decision, a coordinator may grant bounded chunks:

```text
Worker A -> 100 allocations
Worker B -> 100 allocations
Worker C -> 100 allocations
```

Those workers can allocate locally from their leases.

That reduces centralized coordination.

But it introduces new problems.

What if a worker dies with unused capacity?

What if a stale worker continues allocating from a lease that should no longer be valid?

How large should leases be?

Small leases reduce stranded capacity but increase coordination.

Large leases reduce coordination but increase failure exposure.

That tradeoff is precisely the kind of systems problem I want Rush to explore.

### Epochs and fencing

Leases are not enough by themselves.

If ownership changes, an old worker must not be allowed to continue making valid allocations from stale state.

V1.1 therefore plans explicit **epoch/fencing semantics**.

A stale lease holder should be unable to generate new valid allocations once its authority has been superseded.

Fencing does not magically reveal what the failed worker consumed before disappearing.

That uncertainty leads to another principle.

### Conservative reclamation

When usage is uncertain, Rush should prefer:

```text
under-delivery
```

over:

```text
hard-cap violation
```

That is consistent with the philosophy established in Module 1.

Safety comes before perfect utilization.

### Pod optimization

Eventually Rush must choose combinations of creatives that fit an opportunity.

For a 30-second break, candidates might contain:

```text
15s
15s
10s
10s
5s
5s
```

Possible pods include:

```text
15 + 15
15 + 10 + 5
10 + 10 + 5 + 5
```

The pod builder must obey all hard constraints while choosing a useful combination.

V1.1 will use a bounded optimization approach such as dynamic programming or a carefully designed greedy strategy where appropriate.

The goal is not to introduce exotic optimization techniques merely for complexity.

The problem is small and bounded enough that the solution should remain explainable.

### Pacing

A campaign should not simply burn its entire capacity as quickly as possible.

Suppose a campaign is expected to run evenly through a day.

Rush needs some notion of whether it is ahead or behind its expected delivery.

V1.1 will start with a simple proportional controller:

```text
expected_delivery_now
    =
total_target × elapsed_fraction
```

Then:

```text
delivery_error
    =
expected_delivery_now - actual_delivery
```

That error can influence a pacing weight.

The project will not begin with full PID or SHALE simply because production ad systems may use sophisticated controllers.

The first goal is to implement the smallest control mechanism that solves the current problem.

If that mechanism exposes a real weakness, the controller can evolve later.

### Benchmarking the architecture

Rush is a product first.

Benchmarks support the architecture; they are not the product identity.

The eventual benchmark suite should compare things such as:

```text
naive capacity enforcement
vs
central atomic allocation
vs
hierarchical leased allocation
```

and:

```text
linear candidate scans
vs
bitmap candidate discovery
```

and measure real outcomes such as:

```text
throughput
p50 latency
p95 latency
p99 latency
capacity violations
frequency-cap violations
pod fill rate
coordinator operations per request
stranded capacity
pacing error
```

No numbers will be invented in advance.

The results have to come from the actual implementation and a documented workload.

---

# V1.2: adding the asynchronous world

V1.1 intentionally ends at committed decision-level allocation.

V1.2 is where Rush begins to model what happens **after** the decision.

The major addition is Kafka.

Not because every backend project needs Kafka, but because V1.2 introduces a real asynchronous problem that justifies it.

Events may include things such as:

```text
selection
impression confirmation
completion
click
reservation expiry
```

The important engineering work is not merely "send events through Kafka."

It is handling reality:

- duplicate events;
- delayed events;
- consumer restarts;
- idempotent processing;
- reservations that expire;
- confirmations arriving after expiry;
- durable reconciliation;
- safe reclamation of unused capacity.

At that point, allocation becomes closer to a two-stage process:

```text
decision
   ↓
reservation
   ↓
playback confirmation
   ↓
confirmed usage
```

That is a much richer accounting model than V1.1.

It is also deliberately postponed until the synchronous decisioning core is correct.

---

# What Rush is not trying to become

Scope discipline matters.

Rush V1.1 is not trying to become:

- a complete ad exchange;
- an RTB marketplace;
- a recommendation model;
- an attribution platform;
- a video transcoder;
- an HLS/DASH packager;
- an SSAI implementation;
- a frontend dashboard;
- a Kubernetes showcase;
- an ML project.

Those may all be interesting systems.

They are simply different systems.

Rush is strongest when its identity stays clear:

> **A real-time constrained allocation backend for high-concurrency live-event advertising.**

---

# The development philosophy

The project has one rule I want to preserve throughout the build:

> **Understand the mechanism before hiding it behind a framework.**

FastAPI is useful, but FastAPI is not the interesting part.

Redis is useful, but using Redis is not itself a systems accomplishment.

Kafka is useful, but adding Kafka does not automatically make a backend distributed.

The interesting questions are:

```text
What state exists?
Who owns it?
Who may mutate it?
What happens under concurrency?
What happens on retry?
What happens after failure?
Which guarantees are hard?
Which outcomes are merely preferred?
How do we prove the guarantee still holds?
```

That is why every Rush Academy module follows the same seven-question progression:

1. What problem are we solving?
2. How would the naive solution work?
3. Why does the naive solution break?
4. What concept or design solves it?
5. How does Rush implement it?
6. What can go wrong?
7. How do we prove it works?

Module 1 was slightly different because the system did not exist yet.

Its job was to define the world in which every later answer has to make sense.

---

# The roadmap

The planned V1.1 Academy is:

```text
Module 1  Product & Domain Model
Module 2  FastAPI Backend Foundations
Module 3  PostgreSQL & Persistence
Module 4  Decision Engine V1
Module 5  Redis, Frequency Caps & Central Capacity
Module 6  Candidate Indexing
Module 7  Hierarchical Budget Leases
Module 8  Lease Fencing & Failure Semantics
Module 9  Ad-Pod Optimizer & Pacing
Module 10 Production Hardening & Benchmarking
```

V1.2 then extends the system with:

```text
Kafka
temporary reservations
impression confirmation
idempotent consumers
durable reconciliation
duplicate/delayed event handling
consumer recovery
failure testing
```

The point is not to maximize the number of technologies.

The point is to let each technology enter only when the problem demands it.

---

# What Module 1 gave us

After Module 1, Rush finally has a stable identity.

We know:

- who calls it;
- what it owns;
- what it does not own;
- what a campaign is;
- what a creative is;
- how campaigns move through their lifecycle;
- how hard constraints differ from soft preferences;
- what a valid no-fill response looks like;
- what "capacity" means in V1.1;
- why V1.2 needs a richer reservation model;
- how an ad decision flows through the system;
- which correctness invariants later implementations must preserve.

That may seem like a lot of design before writing code.

But this is exactly the point.

Once concurrency, Redis, distributed leases, retries, and failures arrive, vague semantics become expensive.

It is much easier to argue about whether an implementation is correct when the definition of correctness was written down first.

So Module 1 ends without a running API.

But Rush now has something more valuable:

**a contract.**

From Module 2 onward, every implementation decision has to live inside it.

---

## References and further reading

The domain model and lifecycle design were informed by public advertising-system concepts and documentation, including:

- Google Ad Manager — *Review the status of a line item*  
  https://support.google.com/admanager/answer/82991?hl=en
- Google Ads — *About campaign statuses*  
  https://support.google.com/google-ads/answer/1722131/about-campaign-statuses?hl=en-GB
- JioHotstar Engineering — *Journey of an Ad Request: The Hidden Engineering* by Ayush Kumar

The JioHotstar article was especially useful as a real-world reference for concepts such as candidate filtering, frequency caps, pacing, ad-pod selection, direct/programmatic fulfillment, and the latency-sensitive nature of ad decisioning during large traffic spikes.

---

*Next: Module 2 — turning the domain model into the first FastAPI service.*
