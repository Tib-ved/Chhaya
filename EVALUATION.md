# How we test Chhaya

Chhaya phones vulnerable residents during heatwaves and cold waves, asks six questions in their language, sorts them into Red, Amber or Green with fixed rules, and gives health workers a ranked visit list. Ask Chhaya answers a district officer's plain questions from the live data. This page says what we check, how, and what we found.

Every figure below is a count ("x of y", never a percentage), with the date and model it was measured on. The same figures are on the live page, [How we test Chhaya](/evidence) (`/evidence`).

**Which model produced these results.** The runs on 18 October used Claude as a stand-in, because Gemini on Vertex AI was congested that day. Check-in simulations: "Recorded with Claude (claude-haiku-5-5) standing in for Chhaya's Gemini text model, which was congested on Vertex AI that day; residents played by claude-haiku-5-5. Chhaya's real calls run on Gemini Live." Ask Chhaya: "Run with Claude (claude-opus-5-5) standing in for Gemini, which was congested on Vertex AI that day. The deployed Ask Chhaya runs on Gemini. Answers graded by claude-opus-5-5." Earlier Gemini results from 14 October are shown with their own date and model.

## Why we test

A wellness call is only useful if it fails safely.

- **A Red case is missed.** Caught by counting Red residents sorted below Red.
- **An emergency without "call 108".** Caught by checking the line was spoken after the emergency was flagged.
- **Questions skipped.** Caught by counting full calls where all six were asked.
- **The wrong language.** Caught by scoring English, Hindi and Marwari separately.
- **A wrong number or made-up claim from Ask Chhaya.** Caught by "numbers come from the data" and "no made-up claims".
- **Unsafe output or a leaked phone number.** Caught by "safe" and the refusal cases.

## How we test

### Check-in calls with simulated residents

An AI model plays the residents, in English, Hindi and Marwari: 50 conversations in all (11 expected Red, 30 Amber, 9 Green). Each talks to the real agent instructions, tools and end-of-call triage. Nobody is phoned. Recordings are scored offline, with no model, on tier reached against expected, Red residents sorted lower, the six questions in full calls, emergencies flagged, and the 108 line spoken.

### Ask Chhaya

We run `adk eval` (Google's Agent Development Kit) on a fixed dataset: 15 questions. An answer passes only if all six checks clear their thresholds: right data used, correct answer, no made-up claims, numbers from the data, safe, and a chart or table when one is expected. We ran it once. On Gemini it runs in three batches of 5 with pauses, to stay within Vertex AI's limits; the 18 October stand-in run on Claude asked all 15 in one batch.

**Refusals.** A separate set of 22 cases checks that Ask Chhaya declines what it should. We ran it on 14 October and have not re-run it. These prompts push the assistant towards things it must refuse, so we keep them to a minimum.

### Speed

Voice pickup and reply time are read from the service's logs on browser test calls; reply time includes the 700 ms the agent waits to be sure the person has finished and excludes the network to the phone. Ask Chhaya: time per answer, split into topic check, model calls, specialist agents and data tools.

## Results

### Check-in calls with simulated residents

Recorded 18 October 2026 with claude-haiku-5-5 standing in for Chhaya's Gemini text model (commits 3aaba8f and c09a3e7), residents also played by claude-haiku-5-5.

- Conversations at the expected tier: 50 of 50.
- Red residents sorted below Red: 0 of 11.
- By language: English 10 of 10; Hindi 23 of 23; Marwari 17 of 17.
- Full calls where all six questions were asked: 35 of 38.
- Emergencies flagged: 9 of 9.
- Emergencies where the 108 line was spoken: 9 of 9. On the 14 October recordings (gemini-3.8-flash), before we fixed the hang-up rule, it was 5 of 9.

### Ask Chhaya

Run 18 October 2026 with claude-opus-5-5 standing in for Gemini, answers graded by claude-opus-5-5 (commit 09d2b57).

- Answers passing every check: 15 of 15.
- The first run that day passed 11 of 15. Two answers drew the right chart or table but their text never said what it showed; one answered an alert question from the overview instead of the alerts list; and one test question was ambiguous (the dataset has three residents called Kamla Devi, and the assistant rightly asked which one). We changed the instructions (name what a chart or table shows; answer alert questions from the alerts list) and made the question name her ward, then ran all 15 again: 15 of 15.

Earlier Gemini results: on 14 October, gemini-3.8-flash, Ask Chhaya passed 13 of 15 questions.

Refusals, run 14 October 2026 on gemini-3.8-flash, not re-run: 22 of 22 handled correctly.

### Speed

Voice, measured 18 October 2026 on the deployed service with gemini-3.8-live, from 3 browser test calls (34 replies):

- Reply time (the person stops speaking → Chhaya starts): median 2.02 s, 90th percentile 2.48 s, slowest 9.04 s. With a tool call in between: median 2.13 s; without: median 1.40 s. This includes the 700 ms wait to be sure the person has finished.
- Pickup (answered → first word): median 2.95 s. Browser calls open the voice session when answered; phone calls open it while the phone rings, which brought pickup from 4.3–4.6 s to 0.7–0.9 s on 15 October (gemini-3.8-live).

Ask Chhaya, from the 18 October run (Claude stand-in timings, claude-opus-5-5, not Gemini timings):

- Time per answer: median 10.8 s, slowest 22.0 s.
- Typical split per answer (medians, so the parts do not add up to the total): topic check 0.9 s, the main agent's model calls 6.7 s, specialist agents 6.2 s, data tools 0.002 s.

### How we got here

| Date | Change | Before | After |
|---|---|---|---|
| 18 Oct | Ask Chhaya: answers name the chart or table they show, alert questions read the alerts list, an ambiguous test question names its resident (claude-opus-5-5) | 11 of 15 | 15 of 15 |
| 18 Oct | Emergency line (108) spoken before the call ends (claude-haiku-5-5; before: 14 Oct, gemini-3.8-flash) | 5 of 9 | 9 of 9 |
| 17 Oct | Region data request on Cloud Run | about 1.0 s | 0.56 s |
| 17 Oct | Overview start-up script size | 650 kB | 331 kB |
| 17 Oct | Officer's Overview loads faster | 18.8 s | 5–7 s |
| 15 Oct | Voice instruction rewritten as a conversation | 3,464 / 3,320 words | 2,231 / 2,036 words |
| 15 Oct | Voice: session open time | 3.5 s | 2.2 s |
| 15 Oct | Voice: answer to first word, session opened early | 4.3–4.6 s | 0.7–0.9 s |
| 14 Oct | Ask Chhaya topic check moved to Flash-Lite | 1.3–10 s | about 0.9 s |
| 12 Oct | Case notes: low thinking | 7–10 s | 2–5 s |

## How to re-run each evaluation

Run from `backend/` with Python 3.11 or 3.12. These commands call a model and cost real quota, so the test suite does not run them.

**Check-in calls (records, then scores)**

```
GCP_PROJECT=<project> TEXT_MODEL=gemini-3.8-flash python -m scripts.record_ai_calls --batch-size 5 --pause-seconds 180
python -m evals.voice.score
```

The recorder runs 5 conversations at a time with a 3-minute pause, saving after each batch. The scorer needs no model. On 18 October we used the stand-in options `--claude` and `--workers`, run locally with `backend/.venv-claude`, never on Cloud Run.

**Ask Chhaya golden set**

```
GCP_PROJECT=<project> python -m evals.ask_chhaya.run --set golden --batch-size 5 --pause-seconds 180
```

Three batches of 5, 3 minutes apart. The adversarial (refusal) set only runs with `--allow-adversarial`; we have not re-run it since 14 October. The 18 October stand-in run used `--claude --judge-model claude-opus-5-5`, locally with `backend/.venv-claude`, never on Cloud Run. `--claude` runs the same agents from `backend/evals/ask_chhaya/claude_agent`; the app itself has no Claude code.

**Voice speed**

About ten short browser calls on the deployed `/call` page, then:

```
python -m scripts.voice_latency --since <start time, UTC>
```

**Results file**

```
python -m scripts.build_eval_showcase
```

This writes the file behind `/evidence`.

**Stops.** Nothing re-runs by itself after a failure. The recording script stops with exit code 3 if the model provider refuses a request on policy or terms-of-use grounds, and with exit code 5 on a rate limit. The Ask Chhaya runner stops with exit code 3 on a policy refusal and exit code 4 when a batch is incomplete. What was already recorded is kept under its own date.

## What we have not tested yet

- Some of the data is synthetic: residents, calls and the officer's dataset are simulated, so the samples are small (50 simulated check-in calls, 15 assistant questions).
- Rate limits on Vertex AI, and running on the team's own cloud credits, stopped us testing at scale or repeating runs; each evaluation was run once.
- The simulation is text, so speech recognition, accents and phone audio are not tested by it.
- Spoken advice is not yet checked automatically for forbidden content in Hindi or Marwari.
- The 18 October check-in simulations and Ask Chhaya runs used a stand-in model (Claude) while Gemini on Vertex AI was congested; the deployed system runs on Gemini. Ask Chhaya on Gemini was last measured on 14 October (13 of 15).
- In a real deployment we would track: pass@k and consistency across repeated runs; Red-case recall and false-alarm rate against health workers' visit findings; call answer and completion rates; speech-recognition error rate in Hindi and Marwari; reply latency at the 95th percentile under load; throughput (concurrent calls per instance); time from a Red case to a health worker's visit; and cost per completed call.
