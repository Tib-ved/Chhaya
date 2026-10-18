<!-- impeccable:product-schema 1 -->

# PRODUCT.md — Chhaya

## Product

Chhaya runs AI wellness-check phone calls to vulnerable residents (elderly people, people living alone, outdoor workers, people with health conditions) during heatwaves and cold waves in India. When a heat or cold warning opens for a district, Chhaya calls residents in their own language, records their answers, and fixed rules (not the AI model) set each resident's priority tier. Health workers then visit residents in priority order.

## Platform

web

## Users

### Regional Officer
District and ward officials.

- **Context:** at a desk. Desktop-first, but every page must also work on a phone.
- **Light:** indoor.
- **Language:** English only; the language switch is not offered in this view.
- **Literacy:** TBD.
- **Top 3 jobs:**
  1. Track open heat and cold warnings and call results across their area (Overview, Coverage).
  2. Find residents who need a visit now, and check that visits are happening (Urgent cases, resident case dialog, Health workers).
  3. Report on the situation and answer analytical questions (Situation report, Ask Chhaya).

### Field crew
ASHA workers and field teams.

- **Context:** outdoors on phones, often in bright sun.
- **Language:** the interface is in Hindi by default, with a switch to English.
- **Literacy:** TBD.
- **Device class and network quality:** TBD (design for low-end phones; see Constraints).
- **Top 3 jobs:**
  1. See today's visits in priority order (Today's visits).
  2. Open a resident's case to see priority, reasons and call summary before visiting, then record the visit outcome, raise an Amber case to Red, or ask an officer to review (resident case dialog).
  3. Find their way around the ward and to nearby centres (Ward map and centres).

## Surfaces

| Page / surface | Role | Primary job | Mode |
|---|---|---|---|
| Overview | Regional Officer | See heat alerts, call results and charts at a glance | Operate |
| Coverage | Regional Officer | Drill down state > district > ward > resident register | Operate |
| Urgent cases | Regional Officer | Find and act on Red and Amber residents | Operate |
| Try a call | Regional Officer | Test the voice agent in the browser or on a phone | Operate |
| Situation report | Regional Officer | Read a summary of the current situation | Read |
| Health workers | Regional Officer | See health workers and their visits (view only; rows open the resident case, where a case can be reassigned) | Operate |
| Ask Chhaya (right drawer, expands to full-screen overlay) | Regional Officer | Ask read-only analytics questions; answers include charts and tables | Operate |
| Ward overview | Field crew | See the state of their ward | Operate |
| Today's visits | Field crew | Work through today's visits in priority order | Operate |
| Ward map and centres | Field crew | Locate residents and centres | Operate |
| Resident case (dialog) | Both | Priority, reasons, visit status, call summary, transcript, timeline. The officer also gets an "Ask Chhaya for a summary" button | Operate |
| Landing page | Demo visitors | Understand what Chhaya does and choose a view (Regional Officer or Field crew) | Persuade |

## Product truths

1. Tiers are always written "Red", "Amber", "Green": capitalised, never all caps.
2. Red means a health worker should go now. Amber means visit today. Green means no visit is needed today.
3. Warning levels use the same three words. The forecast's "orange" level is always shown as "Amber" (for example "Red heat warning", "Amber alert"). The word "orange" never appears as a warning level.
4. Priority tiers are set by fixed rules, not by the AI model. Copy and UI must never suggest the AI model decided a tier.
5. Wherever a risk score appears, its legend appears with it: aged 65 to 74 +2, aged 75 or over +3, lives alone +2, works outdoors +2, each health condition +1. A score of 5 or more is high risk.
6. Tier is never shown by colour alone. Every tier colour is paired with the tier word.
7. Chhaya is not a diagnosis. No screen may present call results as a medical assessment.
8. When data is demo, simulated or AI-played, the UI says so plainly.
9. The demo-data notice stays pinned under the top bar on every page while demo data is in use.
10. No emoji as icons. Use line icons only.
11. The orange sparkle icon appears only beside "Ask Chhaya" and nowhere else.
12. Field crew text must stay readable in Hindi (Devanagari line height about 1.6) and in bright light.
13. Ask Chhaya is read-only. It never offers, or implies it can take, actions that change data.

## Voice and copy

**Tone:** plain, calm, honest. Short sentences. State facts and next steps. No urgency theatre, even for Red.

**Casing:**
- Use sentence case everywhere: headings, buttons, labels, tabs, eyebrows and sidebar section titles. No CSS `text-transform: uppercase`.
- Tier words and the product name keep their capitals ("Red", "Chhaya") inside sentence-case strings.
- Never use all caps for any text.

**Terminology (use exactly):**

| Term | Use for | Notes |
|---|---|---|
| Regional Officer | The district/ward official role | Capitalised as a role name |
| Field crew | The phone-based field role | Role name in the UI |
| ASHA worker | A specific kind of health worker | "ASHA" is an acronym and stays in capitals |
| health worker | Anyone who visits residents | Generic term; lower case in running text |
| resident | A person Chhaya calls | Never "patient", "user", "beneficiary" or "target" |
| case | A resident's record as opened in the case dialog | |
| round | One calling run for one district on one heat day, opened when that district's warning opens | |
| tier | Red / Amber / Green priority | Never "score", "grade" or "level" for tier |
| risk score | The points-based score with its legend | Separate from tier: the risk score sets who is called first; the tier comes from the call's answers |
| warning / alert | Heat or cold warning for a district | Levels use Red / Amber wording |

Hindi equivalents for all terms above: TBD.

**Banned phrasings:**
- Hype and safety claims: "AI-powered", "smart", "revolutionary", "life-saving", "saves lives", "guaranteed", "ensures safety", "keeps residents safe", "100%", and any safety outcome that has not been measured.
- Medical framing: "diagnosed", "patient", "symptoms detected", "medical assessment". "Diagnosis" appears only in the disclaimer "It is not a diagnosis."
- "Emergency" is allowed only as a plain, factual label (a reported emergency, a resident card tag), never as alarm.
- Words implying the AI decided a tier: "AI flagged", "the AI decided", "AI risk rating".
- "Orange" for a warning level.
- Exclamation marks, "Oops", "Uh-oh", jokes in error states.
- Alarmist wording such as "CRITICAL", "EMERGENCY!!" or "Act now!".

**Honesty labels:**
- Demo data: the notice opens with "Demo data:" and is pinned under the top bar. Officer and crew pages: "Demo data: the residents are fictional; the districts and wards are real (official ward lists)." Outside demo mode it reads "Team view".
- Simulated calls: "Simulated call" (Hindi: सिम्युलेटेड कॉल); visits no one made: "Simulated visit".
- AI voice: Chhaya's turns in every transcript are labelled "Chhaya AI voice agent" (Hindi: छाया एआई वॉइस एजेंट).

## Constraints

**Accessibility**
- Text contrast is at least 4.5:1, measured against the actual background.
- Large text (at least 24px regular or 18.66px bold) and non-text UI parts (control borders, focus rings, chart marks) are at least 3:1.
- Touch targets are at least 44 × 44 px on every surface, not only phone layouts.
- Everything works with a keyboard alone: visible focus on every interactive element, a logical tab order, Esc closes dialogs and the drawer, and focus returns to the element that opened them.
- Use native `<dialog>` with `showModal()` for resident cases.
- Charts have a text or table equivalent.
- Respect `prefers-reduced-motion`.

**Bilingual layout**
- Field crew UI defaults to Hindi, with an English switch.
- Layouts must survive Hindi strings that are longer or taller than English: no fixed-height text boxes, no truncation of tier words, no single-line clamps on labels that carry meaning.
- Devanagari body text uses line height about 1.6.
- Transcripts are shown in the language and script spoken, never translated. They may contain Hindi, English, Marathi, Gujarati or Marwari. Gujarati uses its own script, which the current font stack (Mukta, Noto Sans Devanagari) does not cover (see Open questions).

**Performance on low-end phones**
- Field crew pages must stay usable on low-end phones and slow networks. Exact budgets are TBD.
- Keep these to a minimum on field crew pages: web font weights, JavaScript and chart libraries.
- No heavy shadows, blurs or large images.
- Show content progressively; never block the visit list behind a chart.

## Decisions (resolved from the built app)

- Regional Officer interface: English only.
- Round: one calling run per district per heat day.
- Risk score sets call order; tier comes from the call's answers through fixed rules. Both appear on Coverage and Urgent cases; the risk score also on the Alert page.
- Field crew can record a visit outcome and note, raise an Amber case to Red, and ask an officer to review.
- The landing page exists: a role gateway with a short product description.
- Transcripts stay in the language spoken.
- Nothing in the app deletes data. Danger styling is used only for "Ask an officer to review" and "Raise to Red".
- Health workers page: view only.

## Open questions

1. Literacy levels for Field crew and Regional Officers. Should field crew screens use icons with labels, larger type or audio?
2. Field crew devices: minimum screen size, OS/browser, offline needs and network conditions. What are the performance budgets?
3. Hindi wording for tiers, warning levels, roles and key UI terms (current: लाल / पीला / हरा for tiers; पीली for the Amber warning, which is also the Yellow warning word).
4. Gujarati-script transcripts: which font? Mukta Vaani is a candidate from the same family.
5. When does the demo-data notice disappear (a non-demo deployment only, or also per user)?
