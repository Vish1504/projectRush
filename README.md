# Project Rush

> **Working title** — the project may be renamed before its first stable release.

**Project Rush** is a real-time constrained ad allocation system designed for high-concurrency live-event workloads.

During a major live-streaming event, an ad break can cause thousands of viewers to request advertisements within the same narrow time window. Rush makes allocation decisions while enforcing targeting rules, viewer frequency limits, campaign capacity, and request idempotency without forcing every request through a single globally contended capacity counter.

---

## Overview

Rush models the backend decisioning layer behind live-stream advertising.

A request may look like:

```text
Viewer:           user_4812
Region:           Mumbai
Device:           Android TV
Subscription:     Premium
Available break:  30 seconds
```

The system determines which campaigns are eligible and safely allocates one while respecting:

- targeting eligibility;
- remaining campaign capacity;
- per-viewer frequency caps;
- campaign state;
- request idempotency;
- stale worker state;
- concurrent allocation pressure.

Rush is not an advertising dashboard, campaign-management frontend, or video-delivery platform.

It focuses specifically on the **real-time allocation problem**.

---

## The Problem

Consider a popular campaign nearing exhaustion:

```text
Remaining campaign capacity: 50 impressions
Concurrent eligible requests: 20,000
```

A naive implementation might perform:

```text
read remaining capacity
        ↓
check capacity > 0
        ↓
serve campaign
        ↓
decrement capacity
```

Under concurrency, many workers can observe the same available capacity before any of them update it.

This can cause the system to issue more allocations than the campaign actually owns.

A centralized atomic counter solves the correctness problem, but introduces another one: every allocation for a popular campaign must coordinate through the same shared state.

Rush instead separates:

- **global capacity authority**;
- **worker-local capacity ownership**;
- **request-time allocation enforcement**.

Campaign capacity is issued to workers through bounded durable leases. Workers then spend already-authorized capacity locally while Redis independently enforces request-level constraints.

---

# Current Architecture

```text
                         Decision Request
                                |
                                v
                    +-------------------------+
                    |     FastAPI Worker      |
                    +------------+------------+
                                 |
                 +---------------+---------------+
                 |                               |
                 v                               v
       +--------------------+          +----------------------+
       | Versioned In-Memory|          |     PostgreSQL       |
       |  Inverted Index    |          |                      |
       +---------+----------+          | Campaign config      |
                 |                     | Targeting data       |
                 |                     | Durable lease state  |
                 |                     +----------+-----------+
                 |                                |
                 |                      bounded capacity lease
                 |                                |
                 |                                v
                 |                     +----------------------+
                 |                     | Worker-Local Capacity|
                 |                     |        Pool          |
                 |                     +----------+-----------+
                 |                                |
                 +---------------+----------------+
                                 |
                                 v
                       Candidate Evaluation
                                 |
                                 v
                    +-------------------------+
                    |       Redis Lua         |
                    |                         |
                    | Request idempotency     |
                    | Viewer frequency cap    |
                    | Index-version fence     |
                    +------------+------------+
                                 |
                                 v
                         Allocation Result
```

---

## PostgreSQL

PostgreSQL stores the durable business state for Rush, including:

- campaign configuration;
- targeting relationships;
- configured campaign capacity;
- remaining globally unleased capacity;
- durable campaign lease records.

PostgreSQL acts as the **global authority for campaign capacity**.

It determines how much capacity may enter circulation across all workers.

---

## Durable Campaign Capacity Leasing

Instead of decrementing one globally shared capacity key for every allocation, workers acquire campaign capacity in bounded chunks.

For example:

```text
Campaign capacity:              1000

Worker A lease:                  100
Worker B lease:                  100
Worker C lease:                  100

PostgreSQL remaining unleased:   700
```

The 300 leased units are already considered globally issued.

Each worker can then consume its own capacity locally without coordinating with PostgreSQL on every request.

When a worker's local pool reaches zero, it requests another durable lease.

### Lease Guarantees

Lease issuance is:

- transactional;
- protected by PostgreSQL row locking;
- bounded by remaining unleased capacity;
- idempotent through a unique `lease_request_id`;
- capable of issuing partial final leases.

For example:

```text
Requested lease:          100
Remaining global capacity: 37

Granted lease:             37
Remaining unleased:         0
```

A retried lease request with the same `lease_request_id` returns the original lease rather than deducting capacity again.

---

## Worker-Local Capacity Pools

Each worker maintains an in-memory capacity pool for every campaign it is currently serving.

A typical path becomes:

```text
request
   ↓
reserve one local campaign unit
   ↓
remaining local units > 0?
   |
   +-- yes → continue immediately
   |
   +-- no  → acquire durable PostgreSQL lease
```

This moves campaign-capacity coordination away from the per-request critical path.

---

## Single-Flight Lease Refill

If many requests arrive simultaneously when a worker's local campaign pool is empty, Rush prevents every request from independently requesting a new lease.

Without coordination:

```text
50 concurrent requests
        ↓
50 PostgreSQL lease requests
```

Rush instead uses a per-campaign synchronization mechanism:

```text
50 concurrent requests
        ↓
1 refill owner
        ↓
PostgreSQL lease acquisition
        ↓
local pool replenished
        ↓
waiting requests continue
```

Only one request performs the refill while the remaining requests wait and re-check the local pool.

---

## Redis Allocation Guard

Redis handles shared request-time state that must remain globally consistent across workers.

A Lua script atomically enforces:

- request idempotency;
- viewer frequency caps;
- candidate-index version fencing.

Campaign capacity is intentionally **not** maintained as a Redis per-request counter.

The responsibilities are separated as follows:

```text
PostgreSQL
    → global campaign capacity authority
    → durable lease issuance

Worker memory
    → already-issued campaign capacity
    → local fast-path reservation

Redis
    → request idempotency
    → frequency enforcement
    → index-version validation
```

---

## Request Idempotency

A decision request is identified by a unique `request_id`.

Once a request has successfully produced an allocation, Redis records the selected campaign.

If the same request is retried, Rush returns the previously selected campaign instead of creating another allocation.

This prevents retries from double-counting:

- frequency;
- campaign consumption;
- allocation decisions.

---

## Viewer Frequency Caps

Rush maintains time-bounded viewer/campaign exposure counters in Redis.

For example:

```text
viewer_4812
campaign_42
maximum impressions per hour: 3
```

Redis atomically verifies the current exposure count before recording another allocation.

The counter uses a TTL so old exposure history automatically stops affecting future frequency windows.

---

## Versioned Candidate Index

Scanning every active campaign on every decision request would become increasingly expensive as campaign volume grows.

Rush therefore maintains a **versioned in-memory inverted index** on each worker.

Campaign targeting remains relational in PostgreSQL, while the worker index provides a fast request-path representation for dimensions such as:

- region;
- device;
- subscription tier.

Candidate discovery uses unions within a targeting dimension and intersections across dimensions.

Each published index snapshot carries a version.

Redis stores the currently valid global index version.

Before an allocation mutates shared state, the Lua guard verifies:

```text
worker index version == published index version
```

If the worker is stale:

```text
allocation rejected
      ↓
shared state unchanged
      ↓
worker refreshes index
      ↓
bounded retry
```

This prevents a worker using an outdated targeting snapshot from committing a new allocation.

---

# Allocation Flow

A simplified decision path is:

```text
1. Receive decision request

2. Check for an existing decision for the request_id

3. Verify worker index freshness

4. Discover candidate campaign IDs using the in-memory index

5. Load eligible campaign records from PostgreSQL

6. For each candidate:
      |
      +-- reserve one unit from worker-local capacity
      |
      +-- local pool empty?
              |
              +-- acquire durable PostgreSQL lease
      |
      +-- execute Redis Lua allocation guard
              |
              +-- idempotency
              +-- frequency cap
              +-- index-version fence

7. Return the winning campaign or NO_FILL
```

---

# Failure Semantics

Rush follows one central rule:

> **Uncertainty may waste capacity, but uncertainty must never create capacity.**

Once a worker reserves one unit from its local pool, three outcomes are possible.

## Successful Allocation

```text
local unit reserved
        ↓
Redis returns ALLOCATED
        ↓
unit remains consumed
```

---

## Definitive Rejection

Examples include:

```text
FREQUENCY_CAPPED
STALE_INDEX
ALREADY_PROCESSED
```

Redis has definitively established that the reservation did not create a new allocation.

The worker can therefore safely refund the unit:

```text
local unit reserved
        ↓
Redis definitively rejects
        ↓
refund local unit
```

---

## Ambiguous Failure

Suppose the request reaches Redis but the connection fails before the worker receives the result.

The worker cannot know whether Redis:

```text
A. never executed the allocation

or

B. executed the allocation but the response was lost
```

Refunding the local unit in case B could allow that capacity to be used twice.

Rush therefore does **not** refund capacity after an ambiguous allocation outcome.

```text
local unit reserved
        ↓
Redis result becomes ambiguous
        ↓
do not refund
        ↓
burn the unit
```

This may cause under-delivery.

It cannot create additional campaign capacity.

Rush deliberately prefers **safe under-delivery over potential overspend**.

---

# Capacity Fragmentation

Hierarchical leasing deliberately introduces a trade-off.

Suppose:

```text
PostgreSQL remaining unleased: 0

Worker A remaining locally:   17
Worker B remaining locally:    9
Worker C remaining locally:    0
```

Worker C cannot acquire another lease even though A and B collectively hold 26 unused units.

This means a worker may temporarily return `NO_FILL` while usable capacity exists elsewhere.

Rush accepts this fragmentation in exchange for removing global campaign-capacity coordination from every decision request.

---

# Lease Size Trade-Off

Lease size controls the balance between coordination overhead and capacity fragmentation.

### Larger leases

Provide:

- fewer PostgreSQL refill transactions;
- lower coordination overhead;
- more requests served entirely from worker memory.

But increase:

- capacity stranded after worker failure;
- fragmentation between workers;
- potential under-delivery.

### Smaller leases

Provide:

- better global capacity utilization;
- less capacity stranded per worker.

But require:

- more PostgreSQL coordination;
- more frequent lease refills;
- higher contention under heavy demand.

Rush therefore treats lease size as a configurable operational parameter rather than assuming one universally correct value.

---

# Correctness Invariants

Rush is built around explicit correctness guarantees.

```text
A campaign must never issue more capacity than PostgreSQL
has durably authorized under the documented failure model.

A durable lease request must not deduct campaign capacity twice.

A worker must never consume more local capacity than it has
been issued.

A viewer must not exceed a configured campaign frequency cap.

A duplicate request must not create another valid allocation.

An allocation using a stale candidate-index version must not
mutate shared allocation state.

A definitive rejection may return locally reserved capacity.

An ambiguous allocation outcome must never create reusable
campaign capacity.

Every returned campaign must satisfy applicable targeting,
campaign-state, and delivery-window constraints.
```

---

# Testing

The current automated test suite contains:

```text
55 passing tests
```

The suite covers:

- campaign persistence;
- database constraints;
- relational targeting;
- candidate eligibility;
- frequency-counter behavior;
- request idempotency;
- Redis Lua atomicity;
- concurrent frequency enforcement;
- stale-index rejection;
- bounded index refresh/retry;
- durable lease issuance;
- partial final leases;
- lease-request idempotency;
- local worker capacity pools;
- capacity refunds;
- bounded lease retries;
- no-more-refill behavior;
- single-flight concurrent refill;
- ambiguous Redis failure handling.

The test suite verifies the documented invariants under the currently implemented failure model.

---

# Technology Stack

| Area | Technology |
|---|---|
| Language | Python 3.12 |
| API | FastAPI |
| Validation | Pydantic |
| Database | PostgreSQL |
| Database layer | SQLAlchemy |
| Migrations | Alembic |
| Shared operational state | Redis |
| Atomic request-time logic | Redis Lua |
| Worker-local synchronization | Python threading primitives |
| Testing | pytest |

---

# Engineering Principles

## Correctness Before Throughput

Campaign capacity and viewer frequency constraints must remain valid under concurrent load.

Increasing throughput is useful only if Rush continues to preserve its documented invariants.

---

## Keep the Critical Path Small

Only work required to produce an allocation should block the request.

Analytics, reporting, reconciliation, and downstream processing should remain outside the synchronous decision path whenever possible.

---

## Reduce Global Coordination

Globally shared state can preserve correctness but becomes expensive when thousands of synchronized requests compete for the same resource.

Rush uses bounded local authority where the same invariant can be preserved without requiring global coordination for every request.

---

## Technologies Must Justify Their Existence

PostgreSQL, Redis, Lua, indexing structures, and synchronization mechanisms are introduced only when they solve concrete architectural problems.

---

## Failure Behaviour Is Part of the Design

Worker crashes, stale state, retries, duplicate requests, timeouts, and ambiguous outcomes have explicit semantics.

Failure handling is treated as part of the architecture rather than something added after the happy path.

---

## Prefer Under-Delivery to Overspend

When allocation ownership cannot be established confidently, Rush behaves conservatively.

Unused capacity may occasionally be stranded.

Capacity must never be recreated simply because the system is uncertain.

---

## Performance Claims Must Be Reproducible

Throughput and latency numbers will only be published alongside:

- workload definition;
- campaign count;
- concurrency;
- machine/environment;
- benchmark methodology.

No unmeasured scalability claims are presented as facts.

---

# Future Work

The core real-time allocation architecture is implemented.

The next stage focuses on extending failure recovery, allocation quality, and production readiness.

## Lease Fencing and Safe Reclamation

Worker failures currently strand unused leased capacity rather than risking unsafe reuse.

Future work will introduce:

- lease ownership generations;
- fencing tokens;
- worker-failure detection;
- safe reclamation of unused capacity;
- stale-worker rejection.

This will allow capacity to return to circulation without allowing an old worker to continue spending reclaimed authority.

---

## Ad-Pod Construction

The decision engine will evolve from selecting individual campaigns to constructing complete duration-constrained ad pods.

Planned constraints include:

- available break duration;
- creative duration;
- competitive exclusion;
- campaign priority;
- frequency caps;
- campaign capacity.

A simple greedy strategy will be compared against a bounded optimization approach where appropriate.

---

## Campaign Pacing

Campaign ranking will incorporate expected-versus-actual delivery.

The goal is to prevent campaigns from:

- exhausting too early;
- severely under-delivering;
- receiving disproportionate traffic during spikes.

---

## Production Hardening

The final V1 hardening phase will include:

- high-concurrency load testing;
- latency measurement;
- throughput benchmarks;
- worker crash testing;
- Redis failure injection;
- PostgreSQL failure testing;
- observability;
- structured logging;
- operational metrics;
- deployment documentation;
- reproducible benchmark workloads.

---

# V2

V2 will extend Rush from a synchronous real-time allocation engine into a broader event-driven delivery system.

Planned additions include:

- Kafka event streams;
- impression events;
- completion events;
- click events;
- idempotent consumers;
- durable allocation evidence;
- reservation-versus-confirmed-impression accounting;
- reconciliation;
- Redis state reconstruction;
- crash recovery;
- duplicate and delayed event handling;
- extended failure testing;
- additional observability and deployment tooling.

A major V2 goal is to ensure transient operational state can be reconstructed from durable committed evidence after infrastructure failure.

---

# Roadmap

```text
Current system
 |
 +-- Campaign lifecycle
 +-- PostgreSQL persistence
 +-- Relational targeting
 +-- Real-time decision API
 +-- Viewer frequency enforcement
 +-- Request idempotency
 +-- Redis Lua allocation guard
 +-- Versioned in-memory candidate indexing
 +-- Stale-index fencing and retry
 +-- Durable hierarchical capacity leasing
 +-- Worker-local capacity pools
 +-- Idempotent lease acquisition
 +-- Partial final leases
 +-- Single-flight refill
 +-- Conservative failure semantics
 |
 v
Future V1 work
 |
 +-- Lease fencing
 +-- Safe capacity reclamation
 +-- Worker-failure recovery
 +-- Ad-pod construction
 +-- Competitive exclusion
 +-- Campaign pacing
 +-- Bounded optimization
 +-- Load testing
 +-- Production hardening
 |
 v
V2
 |
 +-- Kafka event streams
 +-- Durable delivery evidence
 +-- Impression / completion / click processing
 +-- Idempotent event consumers
 +-- Redis reconstruction
 +-- Reconciliation
 +-- Crash recovery
 +-- Extended failure testing
```

---

# Repository Status

The core Rush allocation engine is implemented and tested.

The repository currently contains the request-time decision path, PostgreSQL-backed campaign management, relational targeting, versioned candidate discovery, Redis-backed frequency enforcement and request idempotency, stale-index fencing, and durable hierarchical campaign capacity leasing with worker-local capacity pools.

Further work is focused on safe lease reclamation, ad-pod optimization, campaign pacing, production hardening, and reproducible benchmarking.

---

# License

A license will be selected before the first stable public release.
