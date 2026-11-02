# TC4 Scenario Specification: Incident Investigation from a Knowledge Base

**Scenario ID:** TC4  
**Epic:** ANVIL-16 (Demo scenarios + QA)  
**Task Ticket:** ANVIL-31  
**Target Architecture / BSP:** SPARC / LEON3 (`sparc/leon3`) under SIS simulator  
**Knowledge Base Sources:** RTEMS Documentation (`rtems-docs`), Closed RTEMS GitLab Issues, and Confluence Space `RTKB`  
**Output Artifacts:** Incident Investigation Confluence Page, Ranked Hypothesis Report, SIS Reproduction Test Case, Remediation Jira Bug, Knowledge Base Write-Back, and Follow-Up Retrospective Demonstration  

---

## 1. Context and Objective

In TC4, Anvil demonstrates autonomous **incident investigation, human-in-the-loop discrimination, and self-learning** in an embedded real-time environment. Unlike traditional cloud microservices, RTEMS real-time applications do not have heavy application-level APM agents or automated alarm management frameworks. When real-time timing degrades or communication buffers drop, flight software engineers must deduce root causes by synthesizing sparse diagnostic codes, operating system documentation, raw architecture specifications, and historical incident post-mortems.

TC4 demonstrates that the Anvil agent can:
1. Ingest an incident report featuring ambiguous symptoms (`RTEMS_TOO_MANY` from an ISR, Rate Monotonic deadline overruns).
2. Retrieve facts from a noisy multimodal knowledge base (`rtems-docs`, closed GitLab issues, and Confluence space `RTKB`) containing both relevant specifications and misleading look-alikes.
3. **Discriminate between look-alike incidents**: Compare current runtime facts against past post-mortems (e.g., rejecting a historical priority inversion post-mortem because current error codes prove queue saturation).
4. **Compute conclusions from raw architectural facts**: Derive burst exhaustion mathematically rather than reading a pre-computed answer from the knowledge base.
5. Navigate a **human SME rejection and redirect gate**, demonstrating how real-time human oversight guides the investigation.
6. Deterministically reproduce the failure in the SIS simulator harness.
7. File structured remediation bug reports and write back an authoritative post-mortem to `RTKB`.
8. **Demonstrate verified self-learning across two runs**: In a scripted follow-up ticket (Run 2), the agent retrieves and cites Run 1's published post-mortem as a primary source.

> [!IMPORTANT]
> **Corpus Hygiene & Anti-Leak Boundary:** This specification (`docs/scenarios/tc4.md`) exists solely in the project repository and is strictly excluded from knowledge base ingestion. The knowledge base contains only raw architectural facts, general runbooks, historical post-mortems, and unrelated noise. The expected hypotheses and numerical calculations below serve exclusively as the evaluator benchmark rubric.

---

## 2. Incident Scenario: Telemetry Buffer Exhaustion Under Sensor Burst

### Operational Context
A satellite science payload instrument running RTEMS 7 on a SPARC/LEON3 processor acquires science telemetry through an interrupt-driven sensor interface (e.g., SpaceWire / high-speed serial) and dispatches telemetry packets to a storage worker task via an RTEMS Classic API Message Queue (`rtems_message_queue_send`). Concurrently, a Rate Monotonic periodic task (`TM_GUIDANCE`, priority 10, period 50ms) handles flight attitude control.

### Incident Symptoms Reported in Jira
During maximum downlink acquisition simulation, the instrument reports:
1. **Telemetry Packet Loss:** Downlink receiver logs record sequence gaps; telemetry counter registers frame drops.
2. **ISR Directive Failure:** Telemetry interrupt service routine increments an error counter with status code `RTEMS_TOO_MANY` (10) from `rtems_message_queue_send`.
3. **Periodic Deadline Misses:** `TM_GUIDANCE` task logs `RTEMS_TIMEOUT` (6) during `rtems_rate_monotonic_period`, reporting execution jitter exceeding 35ms.
4. **Negative Indicators:** Watchdog timer does not trip; heap memory (`malloc`) reports zero leaks; task stack bounds checker passes cleanly.

---

## 3. Knowledge Base Corpus & Synthetic Asset Architecture

The knowledge base indexes `rtems-docs`, closed GitLab issues, and 10 synthetic Confluence pages in the `RTKB` space. All synthetic pages are titled with the prefix `(synthetic)` per demo hygiene rules.

```mermaid
flowchart TD
    subgraph Space RTKB (Confluence)
        A1["[synthetic] Architecture Note: Payload Telemetry Flow and Queue Specifications"]
        A2["[synthetic] Post-Mortem: 2025 Payload Guidance Timing Overrun Incident"]
        A3["[synthetic] Runbook: Real-Time Deadline Overrun & Directive Failure Triage"]
        N1["[synthetic] Runbook: Flash Memory ECC Single-Bit Error Scrubbing"]
        N2["[synthetic] Meeting Notes: 2026-09-15 Instrument Thermal Vacuum Test Review"]
        N3["[synthetic] Architecture Note: Star Tracker UART Frame Protocol Definition"]
        N4["[synthetic] Post-Mortem: 2024 Power Distribution Unit Relay Chatter Flap"]
        N5["[synthetic] Runbook: Spurious Watchdog Timer Reset Diagnostics"]
        N6["[synthetic] Meeting Notes: 2026-10-02 Downlink Transponder X-Band Calibration"]
        N7["[synthetic] Architecture Note: LEON3 Dual-Ported Memory Bank Partitioning"]
    end
    subgraph Real Existing Pages
        D1["ANVIL-13 evidence (Devin - Leave Untouched)"]
    end
    subgraph Technical Documentation
        R1["RTEMS Docs: Message Manager (c-user/message/)"]
        R2["RTEMS Docs: Rate Monotonic (c-user/rate-monotonic/)"]
        R3["RTEMS Docs: Interrupt Manager (c-user/interrupt/)"]
    end
```

### 1. The Discriminating Look-Alike Post-Mortem
* **Title:** `[synthetic] Post-Mortem: 2025 Payload Guidance Timing Overrun Incident`
* **Content:** Documents a past 2025 incident exhibiting nearly identical top-level symptoms: packet drops and `RTEMS_TIMEOUT` on `TM_GUIDANCE`. However, the root cause was **priority inversion** on a shared data mutex (`RTEMS_PRIORITY` without `RTEMS_INHERIT_PRIORITY`) between a low-priority logging task and guidance. Crucially, the 2025 incident records that `rtems_message_queue_send` always returned `RTEMS_SUCCESSFUL` (0).
* **Investigation Function:** Tests the agent's discrimination ability. If the agent naively retrieves the post-mortem and blames priority inversion, it fails. The agent must contrast the symptoms: the presence of `RTEMS_TOO_MANY` in the current incident rules out the 2025 mutex hypothesis.

### 2. Raw Facts Architecture Note (No Pre-Stated Sizing Conclusions)
* **Title:** `[synthetic] Architecture Note: Payload Telemetry Flow and Queue Specifications`
* **Content:** Provides raw engineering specifications only:
  - Telemetry Message Queue Capacity: $N = 16$ messages.
  - Message Payload: 64 bytes.
  - Sensor Nominal Acquisition Rate: $100\text{ Hz}$ ($10\text{ ms}$ interval).
  - Telemetry Consumer Drain Time: $1.5\text{ ms}$ per packet.
  - Peak Burst Rate: $500\text{ Hz}$ ($2\text{ ms}$ interval) for up to $100\text{ ms}$.
* **Investigation Function:** Forces the agent to perform domain math rather than reading a conclusion:
  $$\text{Queue Capacity in Time} = 16 \text{ messages} \times 2\text{ ms} = 32\text{ ms}$$
  When `TM_GUIDANCE` executes a 50ms periodic cycle with 35ms burst preemption, the queue fills in 32ms, causing subsequent non-blocking ISR sends to return `RTEMS_TOO_MANY` and drop frames.

### 3. General Triage Runbook
* **Title:** `[synthetic] Runbook: Real-Time Deadline Overrun & Directive Failure Triage`
* **Content:** High-level troubleshooting flowchart: mapping `RTEMS_TOO_MANY` to buffer depletion, `RTEMS_TIMEOUT` to period expiration, and distinguishing ISR storms from mutex blocking.

### 4. Noise Pages (7 Unrelated Pages)
To ensure the retrieval step is realistic and challenging, the space contains 7 realistic but unrelated engineering pages (flash memory scrubbing, thermal vacuum meeting notes, star tracker UART protocol, relay chatter, watchdog resets, transponder calibration, dual-ported memory).

---

## 4. End-to-End Investigation Flow (Run 1 & Run 2)

```mermaid
sequenceDiagram
    autonumber
    actor User as Human Evaluator / SME
    participant Jira as Jira (RTEMS Board 139)
    participant Agent as Anvil Agent
    participant KB as Knowledge Base (pgvector)
    participant Conf as Confluence (RTKB)
    participant SIS as SIS Simulator

    Note over Jira,Agent: RUN 1: Incident Investigation & Self-Learning
    Jira->>Agent: Intake RTEMS-101 (Telemetry Drops + Overrun)
    Agent->>KB: Search symptoms (RTEMS_TOO_MANY, deadline overrun)
    KB-->>Agent: Returns 2025 Post-Mortem, Specs, and Docs
    Agent->>Agent: Discriminate: Reject 2025 Mutex (Queue was empty in 2025; here RTEMS_TOO_MANY)
    Agent->>Agent: Calculate Burst Math from Specs (16 msgs * 2ms = 32ms < 50ms period)
    Agent->>Conf: Draft Incident Analysis Page
    Agent->>SIS: Execute tc4_reproduce.c (Confirms RTEMS_TOO_MANY & Missed Period)
    Agent->>User: Request SME Gate: Present Initial Hypotheses
    User-->>Agent: REJECT & REDIRECT: "Verify whether queue depth accounts for burst duration."
    Agent->>Agent: Re-score & emphasize queue capacity vs burst arrival
    Agent->>User: Re-submit SME Gate: Primary Hypothesis validated with math
    User-->>Agent: APPROVE: "Root cause validated. Proceed to bug filing."
    Agent->>Jira: Create Bug RTEMS-102 (Remediation: Queue depth 32 + ISR watermark)
    Agent->>Conf: Publish Final Post-Mortem: 2026 Telemetry Saturation
    Agent->>KB: Ingest New Post-Mortem into pgvector

    Note over Jira,Agent: RUN 2: Self-Learning Demonstration
    Jira->>Agent: Intake RTEMS-103 (Secondary Payload Queue Jitter)
    Agent->>KB: Query "secondary payload queue drops RTEMS_TOO_MANY"
    KB-->>Agent: Returns 2026 Telemetry Post-Mortem (Rank 1 Citation!)
    Agent->>Jira: Resolve RTEMS-103 citing 2026 Post-Mortem guidance
```

### Detailed Execution Breakdown:

#### Stage 1: Symptom Extraction & Multi-Source Search
* Agent reads ticket `RTEMS-101`, identifying core parameters: `RTEMS_TOO_MANY`, `RTEMS_TIMEOUT`, `TM_GUIDANCE`, and telemetry drops.
* Queries KB using hybrid dense-vector + full-text search:
  - Query: `"rtems_message_queue_send RTEMS_TOO_MANY telemetry drop"`
  - Query: `"rate monotonic period overrun guidance deadline"`
  - Query: `"telemetry queue depth burst arrival rate"`

#### Stage 2: Hypothesis Discrimination & Sizing Computation
The evaluator rubric expects the agent to formulate and discriminate:
* **Candidate Hypothesis A (2025 Look-alike: Mutex Priority Inversion):**
  - *Agent Analysis:* 2025 incident post-mortem indicates priority inversion on shared mutex. However, in that incident, queue sends succeeded (`RTEMS_SUCCESSFUL`). In the current incident, `rtems_message_queue_send` returns `RTEMS_TOO_MANY`.
  - *Verdict:* **REJECTED.** Mutex contention does not explain ISR queue exhaustion.
* **Candidate Hypothesis B (Primary: Queue Burst Capacity Exhaustion):**
  - *Agent Analysis:* Architecture specifications state queue depth is 16. Sensor burst rate is 500 Hz (1 message every 2ms). Maximum continuous queue buffering time is $16 \times 2\text{ ms} = 32\text{ ms}$. Guidance task execution jitter delays queue draining by 35ms. The queue overflows at $t = 32\text{ ms}$, generating `RTEMS_TOO_MANY` in the ISR.
  - *Verdict:* **ACCEPTED (Primary).**

#### Stage 3: Simulator Reproduction (`tc4_reproduce.c`)
* The agent writes a targeted standalone test harness `tc4_reproduce.c` compiling under RTEMS 7 SPARC/LEON3.
* The test creates a 16-message queue and a 50ms Rate Monotonic task. An ISR timer injects 500 Hz messages.
* Run on SIS:
  ```bash
  sis -leon3 -m 32 tc4_reproduce.exe
  ```
* Output logs capture explicit occurrences of `rtems_message_queue_send -> RTEMS_TOO_MANY` and `rtems_rate_monotonic_period -> RTEMS_TIMEOUT`.

#### Stage 4: Human-in-the-Loop SME Review Gate (Interactive Demonstration)
To show the evaluators that the human gate is active and impactful:
1. **First Submission:** The agent presents its analysis with equal weighting between queue exhaustion and historical priority inversion.
2. **SME Rejection & Redirect:** The human evaluator / SME replies via Jira comment / comms:
   > *"REJECT: Look closely at the directive error code RTEMS_TOO_MANY in the ISR trace. The 2025 incident had RTEMS_SUCCESSFUL. Re-evaluate queue capacity against burst arrival rates."*
3. **Agent Adaptation:** The agent acknowledges the feedback, recalculates the queue exhaustion timeline, establishes queue saturation as the definitive root cause, and re-submits.
4. **SME Approval:**
   > *"CONFIRMED: Root cause validated. Proceed to bug filing and KB update."*

#### Stage 5: Remediation Filing & Knowledge Base Update
* **Jira Defect Filed:** `RTEMS-102: Telemetry queue capacity inadequate for 500Hz burst acquisition during guidance cycles`.
* **Confluence Post-Mortem Published:** `[synthetic] Post-Mortem: 2026 Telemetry Queue Saturation & Burst Overrun`.
* **KB Indexing:** Ingests the new Confluence page into PostgreSQL (`anvil`).

#### Stage 6: Run 2 — Self-Learning Verification
* A follow-up ticket `RTEMS-103: Secondary payload telemetry dropped under burst mode` is opened.
* The agent executes `kb_search`.
* **Verification Proof:** The newly created 2026 Post-Mortem appears as **Rank 1** in the search results with high relevance score ($>0.030$). The agent directly cites the 2026 incident findings to prescribe sizing recommendations immediately, proving that the factory retains and leverages learned institutional knowledge.

---

## 5. Required Deliverables & Acceptance Criteria

| Deliverable | Requirement | Verification Method |
|---|---|---|
| **Incident Scenario Specification** | `docs/scenarios/tc4.md` merged to `main` | Peer review & PR merge |
| **Synthetic Confluence Seed Pages** | 10 synthetic pages published in `RTKB` with `(synthetic)` titles | Confluence API inspection |
| **Discrimination Evidence** | Agent report explicitly analyzes and rejects the 2025 Mutex hypothesis | Confluence report contains discrimination section |
| **Burst Math Verification** | Agent calculates queue capacity ($16 \times 2\text{ ms} = 32\text{ ms}$) without pre-existing text | Comparison of agent output against formula |
| **SME Gate Demonstration** | Documented rejection/redirect cycle resulting in adjusted hypothesis | Jira comment / audit log trace |
| **SIS Simulator Reproduction** | `tc4_reproduce.exe` demonstrates `RTEMS_TOO_MANY` and `RTEMS_TIMEOUT` | Terminal log from SIS execution |
| **Self-Learning Demonstration** | Run 2 retrieves and cites Run 1 post-mortem at Rank 1 | Search query log and citation in ticket RTEMS-103 |

---

## 6. Seed RTEMS Jira Tickets for Demo

### Ticket 1: Initial Incident (Run 1)
* **Project:** `RTEMS` (Board 139)
* **Summary:** `[TC4] Incident: Telemetry packet drops and TM_GUIDANCE deadline overrun under burst traffic`
* **Labels:** `incident-investigation`, `tc4` *(omit `anvil` label until demo execution)*
* **Description:**
  ```text
  h2. Incident Summary
  During high-rate sensor burst simulation on the LEON3 payload platform, telemetry receiver logs record packet losses accompanied by real-time deadline misses on the periodic guidance task.

  h2. Observed Symptoms
  1. Downlink receiver logs record dropped frames during 500Hz burst acquisition.
  2. Telemetry ISR error counter increments with status code RTEMS_TOO_MANY (10).
  3. Task TM_GUIDANCE (priority 10, period 50ms) reports RTEMS_TIMEOUT (6) from rtems_rate_monotonic_period, indicating missed deadlines (>35ms delay).
  4. Stack monitoring and heap integrity checks report nominal status (no memory leaks or overflows).

  h2. Requested Action
  1. Query the engineering knowledge base to identify potential root causes for concurrent RTEMS_TOO_MANY and rate-monotonic deadline misses.
  2. Produce a ranked hypothesis report citing relevant RTEMS specifications and past operational runbooks.
  3. Prepare an incident analysis page on Confluence (RTKB).
  4. Reproduce the failure mode under the SIS simulator.
  5. Present findings to the lead engineer for confirmation before logging design remediation.
  ```

### Ticket 2: Follow-Up Retrospective (Run 2 - Self-Learning)
* **Project:** `RTEMS` (Board 139)
* **Summary:** `[TC4] Secondary payload instrument reports intermittent telemetry packet drops during burst mode`
* **Labels:** `incident-investigation`, `tc4-followup` *(omit `anvil` label until demo execution)*
* **Description:**
  ```text
  h2. Incident Summary
  The secondary payload instrument interface reports intermittent packet loss under 500Hz burst conditions. Error logs indicate rtems_message_queue_send returning RTEMS_TOO_MANY.

  h2. Requested Action
  Query the knowledge base for recent telemetry queue buffer investigations, review prior post-mortems, and recommend verified sizing adjustments.
  ```
