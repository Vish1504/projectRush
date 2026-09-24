# Project Rush

> **Working title** — the project will be renamed before its first stable release.

**Project Rush** is a real-time constrained ad allocation platform designed for high-concurrency live-event workloads.

During a large live-streaming event, an ad break can cause thousands of viewers to request advertisements within the same narrow time window. Rush is designed to construct valid ad pods while enforcing campaign capacity, targeting rules, viewer frequency limits, pacing, and competitive constraints — without allowing a popular campaign to become a centralized contention bottleneck.

> **Current milestone:** V1.1  
> **Status:** 🚧 Active development

---

## Overview

Rush models the backend decisioning layer behind live-stream advertising.

Given an ad opportunity such as:

```text
Viewer:           user_4812
Region:           Mumbai
Device:           Android
Subscription:     Premium
Available break:  30 seconds
```

the system determines which eligible advertisements should be returned while respecting campaign-level and viewer-level constraints.

A typical decision must consider:

- targeting eligibility;
- remaining campaign capacity;
- per-user frequency caps;
- creative duration;
- competitive exclusion;
- campaign priority;
- delivery pacing;
- decision latency.

The goal is not to build an advertising dashboard or a video-delivery platform.

Rush focuses specifically on the **real-time allocation problem**.

---

## The Problem

Consider a popular campaign with only a small amount of capacity remaining:

```text
Remaining campaign capacity: 50 impressions
Concurrent eligible requests: 20,000
```

Thousands of decision workers may attempt to allocate the same campaign at nearly the same time.

A naive read-check-write implementation can allow multiple workers to observe the same available capacity and collectively overspend it.

A fully centralized atomic allocator can preserve correctness, but every request must coordinate through the same shared state, creating a potential hot-key bottleneck under synchronized demand.

Rush is being designed to preserve hard allocation constraints while reducing unnecessary coordination on the critical decision path.

---

## Planned V1.1 Architecture

```text
                         Ad Decision Request
                                  |
                                  v
                       +---------------------+
                       |   Decision Service  |
                       +----------+----------+
                                  |
                                  v
                       +---------------------+
                       | Candidate Discovery |
                       +----------+----------+
                                  |
                                  v
                       +---------------------+
                       | Targeting /         |
                       | Eligibility         |
                       +----------+----------+
                                  |
                                  v
                       +---------------------+
                       | Frequency Caps      |
                       +----------+----------+
                                  |
                                  v
                       +---------------------+
                       | Campaign Capacity   |
                       +----------+----------+
                                  |
                                  v
                       +---------------------+
                       | Pacing / Ranking    |
                       +----------+----------+
                                  |
                                  v
                       +---------------------+
                       | Ad-Pod Construction |
                       +----------+----------+
                                  |
                                  v
                           Decision Response
```

The planned V1.1 design separates responsibilities across several storage and execution layers:

- **PostgreSQL** — durable campaign configuration and persistent business state;
- **Redis** — low-latency operational state such as frequency caps and campaign allocation coordination;
- **in-memory candidate indexes** — fast campaign discovery on the request path;
- **decision workers** — targeting, ranking, capacity enforcement, and ad-pod construction.

High-contention campaign capacity will later be distributed to workers through bounded leases with fencing semantics, allowing most allocations to occur without contacting the global coordinator on every request.

---

## V1.1 Scope

V1.1 is the first portfolio-ready release of Rush.

### Campaign Management

Create, update, activate, pause, and inspect campaigns with configurable:

- targeting rules;
- creative duration;
- delivery capacity;
- frequency limits;
- priority;
- delivery window;
- competitive category.

### Real-Time Decision API

Given a viewer and an available ad-break duration, return a valid ad pod satisfying all applicable campaign and viewer constraints.

### Frequency Enforcement

Prevent a viewer from receiving the same campaign beyond its configured exposure limit.

### Campaign Capacity Management

Prevent campaigns from issuing more valid allocations than their available capacity.

The first implementation will establish a correct centralized allocator before introducing hierarchical worker leases.

### Candidate Indexing

Replace repeated full scans of active campaigns with inverted bitmap indexes for common targeting dimensions.

### Hierarchical Budget Leasing

Distribute bounded campaign capacity to decision workers so that most allocations can be completed locally rather than requiring central coordination for every request.

### Lease Fencing

Associate leases with generation or epoch information so that stale workers cannot continue issuing valid allocations after losing ownership of a lease.

### Ad-Pod Construction

Construct duration-constrained ad pods while respecting:

- targeting;
- capacity;
- frequency caps;
- competitive exclusion;
- creative duration.

A greedy baseline will be compared with a bounded dynamic-programming approach where appropriate.

### Campaign Pacing

Adjust campaign ranking according to expected versus actual delivery so campaigns remain close to their intended delivery trajectory over time.

---

## Technology Stack

| Area | Technology |
|---|---|
| Language | Python 3.12 |
| API | FastAPI |
| Validation | Pydantic |
| Database | PostgreSQL |
| Database layer | SQLAlchemy |
| Migrations | Alembic |
| Hot operational state | Redis |
| Testing | pytest |
| Load testing | k6 / Locust / custom workload generator |
| Local environment | Docker Compose |

### Why Kafka Is Not in V1.1

Kafka is intentionally excluded from the initial critical path.

V1.1 focuses on synchronous ad decisioning and allocation correctness.

Kafka will be introduced in V1.2 for asynchronous workflows such as:

- impression processing;
- completion events;
- click events;
- durable reconciliation;
- reporting;
- consumer recovery;
- duplicate-event handling.

---

## V1.2

V1.2 will extend Rush into a more complete event-driven backend.

Planned additions include:

- Kafka event streams;
- impression, completion, and click events;
- idempotent consumers;
- durable reconciliation;
- reservation-versus-confirmed-impression accounting;
- consumer crash recovery;
- delayed and duplicate event handling;
- extended failure testing;
- additional observability and deployment tooling.

---

## Core Engineering Principles

### Correctness Before Throughput

Campaign capacity and frequency constraints must remain valid under concurrent load.

Increasing throughput is useful only if the system continues to preserve its documented invariants.

### Keep the Critical Path Small

Only work required to produce an ad decision should block the request.

Analytics, reporting, reconciliation, and other downstream processing should remain outside the synchronous decision path whenever possible.

### Technologies Must Justify Their Existence

Redis, PostgreSQL, Kafka, indexing structures, and distributed coordination mechanisms are introduced only when they solve a concrete architectural problem.

### Failure Behaviour Is Part of the Design

Worker crashes, stale leases, retries, duplicate events, and partial failures should have explicit semantics rather than undefined behaviour.

### Performance Claims Must Be Reproducible

Throughput and latency numbers will only be published alongside:

- workload definition;
- campaign count;
- concurrency;
- hardware/environment;
- benchmark methodology.

No unmeasured scalability claims will be made.

---

## Correctness Invariants

Rush will explicitly document the guarantees expected from the system.

Examples include:

```text
A campaign must not issue more valid allocations than its
available capacity under the documented failure model.

A viewer must not exceed a configured campaign frequency cap.

A stale or superseded lease must not create new valid allocations.

Every returned creative must satisfy campaign targeting rules.

Mutually exclusive advertisers must not appear in the same ad pod.

Ad-pod duration constraints must always be respected.
```

The full set of invariants and failure assumptions will be maintained separately as the implementation evolves.

---

## Development Roadmap

```text
V1
 |
 +-- Campaign lifecycle
 +-- PostgreSQL persistence
 +-- Basic targeting
 +-- Decision API
 +-- Frequency caps
 +-- Centralized capacity allocation
 +-- Basic ad-pod construction
 |
 v
V1.1
 |
 +-- Inverted bitmap candidate indexing
 +-- Hierarchical capacity leases
 +-- Lease fencing
 +-- Concurrency hardening
 +-- Campaign pacing
 +-- Bounded ad-pod optimization
 +-- Load testing
 +-- Production-oriented documentation
 |
 v
V1.2
 |
 +-- Kafka
 +-- Event-driven impression processing
 +-- Idempotent consumers
 +-- Durable reconciliation
 +-- Recovery and failure testing
```

---

## Development Approach

Rush is being built incrementally through ten focused engineering modules:

1. Product & Domain Model
2. FastAPI Backend Foundations
3. PostgreSQL & Persistence
4. Decision Engine
5. Redis, Frequency Caps & Capacity
6. Candidate Indexing
7. Hierarchical Budget Leases
8. Fencing & Failure Semantics
9. Ad-Pod Optimization & Pacing
10. Production Hardening & Benchmarking

Each module begins with the same set of design questions:

1. What problem are we solving?
2. What is the simplest implementation?
3. Where does that implementation break?
4. What concept addresses the limitation?
5. How should Rush implement it?
6. What failure modes must be considered?
7. How will correctness or performance be demonstrated?

Design notes will live under [`docs/`](./docs/) as the project progresses.

---

## Current Milestone

The current target is:

> **Rush V1.1 — a deployable real-time ad allocation service with high-concurrency capacity management, indexed candidate discovery, frequency enforcement, pacing, and constrained ad-pod construction.**

Target development window: **10 focused working days**.

---

## Repository Status

The project is currently in early development.

The following will be added incrementally as the implementation matures:

- API documentation;
- architecture diagrams;
- database schema documentation;
- correctness invariants;
- failure model;
- benchmark methodology;
- measured performance results;
- deployment instructions.

---

## License

A license will be selected before the first public release.

