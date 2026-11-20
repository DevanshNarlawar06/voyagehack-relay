# Relay: 2-minute demo video script

The video follows **one story**: a new hire, **Diya Kapoor (Product Manager)**, joining TBO. Every screen you open shows a different part of that one journey.

You need two things open before you start:

- the live site: https://relay-hr-console.vercel.app
- the architecture picture: [docs/images/architecture.png](images/architecture.png)

---

## Part 1: Understand it in 3 minutes (read before recording)

**One sentence.** Relay is HR software where AI agents do the boring cross-team work of onboarding a new employee, people only make the important decisions, and every action is checked against company rules, logged and costed.

**What it replaces.** Today HR emails IT for a laptop, Admin for an access card and Finance for payroll, then chases each one. In Relay, one event ("offer accepted") starts a **run** that does all of that by itself.

**The six words you will say in the video:**

| Word | What it means | Where you see it |
|---|---|---|
| **Run** | One journey for one person, e.g. Diya's onboarding | Runs page |
| **Step** | One box in the run: "Order laptop", "Set up payroll" | Run detail graph |
| **Policy** | A company rule written as a small file, e.g. "laptops above ₹1.5L need approval" | Policies page |
| **Reserved decision** | A decision only a named human may take (probation, resignation, offer). The AI can never approve it | Inbox |
| **Audit** | A tamper-proof log of everything that happened, and why | Audit page |
| **Kill switch** | A pause button for the whole system or one part | Controls page |

**The one idea that wins judges:** before *every* step, Relay asks the policy engine "is this allowed?". The answer is **ALLOW** (do it), **DENY** (stop) or **REQUIRE_HUMAN** (send it to a person). Every answer is written to the audit log. This is the "Life of one step" picture in the README.

**About the "23 architectures".** Relay has **one** architecture, the single picture in `architecture.png`. Two things may have looked like many:

- The hackathon offers many problem statements, and we did **PS6 (Darwinbox)**.
- The software has many screens and parts.

For the video you show **one journey (onboarding)** end to end. You also mention once that **Exit** and **DPDP data requests** run on the same core without changing it. That proves it is a platform.

---

## Part 2: Before you press record (2 minutes)

1. Open https://relay-hr-console.vercel.app/welcome in Chrome at 100% zoom, full screen, light mode.
2. **Refresh the page right before recording.** The demo data resets on every refresh, so earlier clicks are undone.
3. Close other tabs and notifications.
4. Do one silent practice run through the click path below.
5. Speak at a calm pace. The script is about 290 words, which fits 2 minutes.

---

## Part 3: The script

Do the **CLICK** lines, and read the **SAY** lines out loud.

### 0:00 – 0:12 · The problem (Welcome page)

**SCREEN:** The welcome page, with "HR work that runs itself…"

**SAY:**
> "Today an HR system only *records* a new hire. People still email IT for the laptop, Admin for the access card, Finance for payroll, and chase each one. This is Relay. Agents close those loops; people make the decisions that matter."

**CLICK:** **Sign in to the console**.

### 0:12 – 0:18 · Sign in

**CLICK:** Under "or use a demo account", click **Meera Nair** (HR Admin).

**SAY:**
> "I'm Meera from HR."

### 0:18 – 0:32 · Dashboard: the numbers

**SCREEN:** The dashboard.

**SAY:**
> "First, the numbers the brief asked for: over 90% of steps finish with no human touch, escalation rate, cycle time, and the cost of every workflow in rupees, measured per step, not guessed."

**POINT AT:** Step automation **91.4%**, then Cost per workflow **₹10.13**.

### 0:32 – 0:55 · One onboarding run (the heart of the demo)

**CLICK:** **Runs** in the sidebar, then **Diya Kapoor · Product Manager**.

**SAY:**
> "Here's Diya's onboarding. One accepted offer started all of this. Relay checked her documents, then in parallel created her login, issued her access card, set up payroll and PF, and briefed her manager. No emails."

**CLICK:** The orange box **Order laptop**.

**SAY:**
> "But her laptop costs over 1.5 lakh. Before every step Relay asks the policy engine, and company policy says this needs IT spend approval. So it's waiting for a person, and it tells you exactly which rule and which policy version."

**POINT AT:** "Policy decision: REQUIRE_HUMAN" and "Why: Laptops above ₹1.5L…".

### 0:55 – 1:15 · Inbox: humans decide, with context

**CLICK:** **Inbox**.

**SAY:**
> "Anything that needs a person lands here with the context to decide. Confirming probation or accepting a resignation are *reserved* decisions: only the named manager can make them. The AI can never approve these, whatever its prompt says."

**SCROLL** to **PAN name does not match the offer**.

**SAY:**
> "And when data conflicts, the agent doesn't guess. It explains: a middle name was dropped. I decide in one click."

**CLICK:** **Accept document name (suggested)**.

### 1:15 – 1:32 · Policies: rules live outside the AI

**CLICK:** **Policies**. **Approvals** is already selected.

**SAY:**
> "The rules don't live inside AI prompts. They're versioned policy files HR can edit."

**EDIT** in the text box:

- change `version: 1` to `version: 2`
- change `150000` to `100000`

**CLICK:** **Run tests**. The page shows "3 of 3 passed".

**SAY:**
> "I lower the laptop limit to one lakh, the tests pass, and I can activate it. Every change is versioned and on the record."

### 1:32 – 1:42 · Controls: fails safely

**CLICK:** **Controls**, then **Inject** next to **Assets**. It turns red ("503").

**SAY:**
> "If a system goes down, like the asset system here, runs wait and retry without ever ordering two laptops. And this button pauses everything instantly."

**POINT AT:** **Pause all runs** (don't click it).

### 1:42 – 1:52 · Audit: what the AI saw

**CLICK:** **Audit**, then the row **documents.extract**.

**SAY:**
> "Every action is in a tamper-proof, hash-chained log: who or what acted, under which policy and model. And here is exactly what left our system to the AI, with personal data already masked."

**POINT AT:** The "What left our system" box: `<PERSON_1>`, `<PAN_1>`.

### 1:52 – 2:00 · Close: a platform

**SCREEN:** Optionally cut to `architecture.png` for the last line.

**SAY:**
> "Onboarding runs in depth, and Exit and data-privacy requests were added on the same core without changing it. Relay: HR work that runs itself, with people on the decisions that matter."

---

## If you have 30 seconds more

Add one of these. Each one fits after the Audit section.

- **Ask Relay** (Slack-style chat).
  - **CLICK:** **Ask Relay**, then the suggestion **"How many leaves do I get in probation?"**
  - **SAY:** "Employees ask in plain language, and the answer cites the policy it came from. The same works in Slack."
- **Employees.**
  - **CLICK:** **Employees**, then **Kiran Rao**.
  - **SAY:** "The system of record from hire to exit. Kiran resigned, and the exit run is waiting on the manager's reserved decision."
- **Architecture slide** (show `architecture.png`).
  - **SAY:** "Three ways in: console, Slack and the joiner's upload link. A deterministic engine that is not an AI, and a policy check before every step. Agents only reach the model through a privacy gateway. Everything sits in one Postgres database."

## Likely judge questions, with short answers

- **"Can the AI approve an offer if someone tricks the prompt?"** No. Reserved decisions have a floor written in code. Config can only add to the list, and agents can never get ALLOW on them.
- **"What if IT's system is down?"** The step waits and retries. An idempotency ledger guarantees the laptop is ordered once. After the SLA, a person gets a task with the context.
- **"How do you add a new HR process?"** Write a workflow file, policy file and rule functions. No core change. Exit and DPDP were built that way, and CI blocks core changes.
- **"Which AI model?"** Google Gemini Flash, through a router with a PII gateway, plus an offline mock so it runs with no key. Most steps use no AI at all. Rules do the deciding.
- **"What does it cost?"** It's measured per step. The model cost per onboarding is a fraction of a rupee, because most steps are rules.
