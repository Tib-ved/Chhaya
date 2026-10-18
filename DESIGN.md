---
name: Chhaya
description: A calm control room for heatwave wellness calls, in the colours of shade and sun.
colors:
  ground-cream: "#FAF8F1"
  surface: "#FFFFFF"
  surface-sunken: "#F3F0E6"
  hairline: "#E4E0D3"
  hairline-strong: "#CFCABB"
  control-line: "#77887C"
  ink: "#0C333B"
  ink-secondary: "#39564A"
  ink-muted: "#53645B"
  shade-green: "#146536"
  shade-green-deep: "#0D4C28"
  shade-green-tint: "#EDF5E8"
  sun: "#F68B1F"
  tier-red: "#B3261E"
  tier-red-ink: "#8C1D17"
  tier-red-tint: "#FBE9E7"
  tier-amber: "#99600A"
  tier-amber-ink: "#6F4200"
  tier-amber-tint: "#FFF0D8"
  tier-green: "#237443"
  tier-green-ink: "#17553A"
  tier-green-tint: "#EAF5E6"
  data-reached: "#1D4E89"
  data-unreached: "#8B5A2B"
  data-pending: "#8A948F"
  data-visits: "#4F5D75"
  info-blue: "#1D4E89"
  info-blue-tint: "#EAF1F9"
typography:
  display:
    fontFamily: "IBM Plex Sans, Mukta, system-ui, sans-serif"
    fontSize: "36px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.02em"
    fontFeature: "tnum"
  headline:
    fontFamily: "IBM Plex Sans, Mukta, system-ui, sans-serif"
    fontSize: "28px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.015em"
  title:
    fontFamily: "IBM Plex Sans, Mukta, system-ui, sans-serif"
    fontSize: "18px"
    fontWeight: 600
    lineHeight: 1.3
  body:
    fontFamily: "IBM Plex Sans, Mukta, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.5
  small:
    fontFamily: "IBM Plex Sans, Mukta, system-ui, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.45
  label:
    fontFamily: "IBM Plex Sans, Mukta, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
  mono:
    fontFamily: "IBM Plex Mono, ui-monospace, monospace"
    fontSize: "13px"
    fontWeight: 500
    lineHeight: 1.4
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  full: "999px"
spacing:
  1: "4px"
  2: "8px"
  3: "12px"
  4: "16px"
  5: "24px"
  6: "32px"
  7: "48px"
components:
  button-primary:
    backgroundColor: "{colors.shade-green}"
    textColor: "{colors.surface}"
    rounded: "{rounded.md}"
    padding: "10px 18px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.shade-green-deep}"
  button-secondary:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.shade-green}"
    rounded: "{rounded.md}"
    padding: "10px 18px"
    height: "44px"
  button-danger:
    backgroundColor: "{colors.tier-red-tint}"
    textColor: "{colors.tier-red-ink}"
    rounded: "{rounded.md}"
    height: "44px"
  card:
    backgroundColor: "{colors.surface}"
    rounded: "{rounded.lg}"
    padding: "20px"
  tier-pill-red:
    backgroundColor: "{colors.tier-red-tint}"
    textColor: "{colors.tier-red-ink}"
    rounded: "{rounded.full}"
    typography: "{typography.label}"
  tier-pill-amber:
    backgroundColor: "{colors.tier-amber-tint}"
    textColor: "{colors.tier-amber-ink}"
    rounded: "{rounded.full}"
    typography: "{typography.label}"
  tier-pill-green:
    backgroundColor: "{colors.tier-green-tint}"
    textColor: "{colors.tier-green-ink}"
    rounded: "{rounded.full}"
    typography: "{typography.label}"
  input:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    height: "44px"
---

# Design System: Chhaya

## Overview

**Creative North Star: "The calm control room"**

Chhaya is used by officials triaging vulnerable residents during a heat emergency, and by field crews on phones in the sun. The interface behaves like a well-run incident console: quiet chrome, dense and legible lists, one clear answer at the top of every screen to "who needs help now?". Colour is reserved for meaning. Calm is the point: even a Red warning is stated plainly, never dramatised.

The palette comes from the logo: a cream ground (shade), a deep shade green (the tree) and a single sun orange that appears only as the brand accent and beside "Ask Chhaya". Everything else is ink on cream and white. Structure comes from hairlines, spacing and type weight, not from boxes inside boxes.

Density is "operations": tables and queues are compact (14px rows at 44px height), figures use tabular numerals, and the page reads top-down in order of urgency: status, triage, then analysis.

**Key Characteristics:**
- Cream ground, white working surfaces, hairline borders, no decorative shadows.
- Tier colours (Red, Amber, Green) mean tiers and nothing else; charts use a separate data palette.
- IBM Plex Sans for interface text and figures, Mukta for Devanagari.
- Every screen leads with its status and its most urgent work; analysis comes after.

## Colors

Shade and sun: a warm cream ground, deep green ink and brand, one orange accent, and a strict tier palette.

### Primary
- **Shade Green** (#146536): the brand. Primary buttons, active navigation, links, focus rings. Hover/pressed: **Deep Shade** (#0D4C28). Tint **Leaf Wash** (#EDF5E8) for selected rows, active tabs and the demo-data band.

### Secondary
- **Sun** (#F68B1F): the logo's sun. Used only for the brand mark and the sparkle beside "Ask Chhaya". Never for status, borders, bands or backgrounds.

### Tiers (status, normative)
- **Tier Red** (#B3261E, ink #8C1D17, tint #FBE9E7): "a health worker should go now"; also the Red warning level.
- **Tier Amber** (#99600A, ink #6F4200, tint #FFF0D8): "visit today"; also the Amber warning level (the forecast's "orange").
- **Tier Green** (#237443, ink #17553A, tint #EAF5E6): "no visit needed today".

### Data (charts, never tiers)
- **Reached** (#1D4E89), **Unreached / no answer** (#8B5A2B), **Pending / still trying** (#8A948F), **Visits** (#4F5D75). Any chart series that is not a tier uses these.
- **Info Blue** (#1D4E89, tint #EAF1F9): flags raised by a worker. Not used for "Heat scenario" or "Simulated" labels, which use the neutral outline pill.

### Neutral
- **Cream Ground** (#FAF8F1): the page background everywhere.
- **Surface** (#FFFFFF): cards, tables, dialogs, the drawer, the top bar.
- **Sunken** (#F3F0E6): table headers, inset rows, legends, code and quotes.
- **Hairline** (#E4E0D3) for dividers and card borders; **Strong hairline** (#CFCABB) for table header rules; **Control line** (#77887C) for input and secondary-button borders (3:1 on white).
- **Ink** (#0C333B) for text; **Ink secondary** (#39564A) for supporting text; **Ink muted** (#53645B) for meta (4.5:1 on cream and white).

### Named Rules
**The Tier Colour Rule.** Red, Amber and Green appear only where they mean a tier or warning level, and always with the word. Calls reached, no answer, coverage and every other series use the data palette.

**The One Sun Rule.** Orange appears in the logo and beside "Ask Chhaya". Nowhere else.

## Typography

**Interface Font:** IBM Plex Sans (with Mukta, system-ui)
**Devanagari Font:** Mukta (with Noto Sans Devanagari)
**Mono Font:** IBM Plex Mono, for rule ids and machine values only

**Character:** Plex Sans is engineered and plain, with excellent tabular figures for an operations console; Mukta carries Hindi with the same calm weight.

### Hierarchy
- **Display** (600, 36px, 1.1, tabular): headline figures in the status strip only.
- **Headline** (600, 28px, 1.2): the page title (h1), once per page.
- **Title** (600, 18px, 1.3): card and section titles (h2).
- **Body** (400, 16px, 1.5): running text, form values. Max measure 70ch.
- **Small** (400, 14px, 1.45): table cells, supporting lines, card subtitles.
- **Label** (500, 12px, 1.4, sentence case): field labels, eyebrows, table headers, pill text. Never uppercase.

Devanagari (`:lang(hi)`) uses Mukta at the same sizes with line-height 1.6 and no letter-spacing.

### Named Rules
**The Seven Sizes Rule.** Only 12, 14, 16, 18, 22, 28 and 36px exist. 22px is for dialog titles. No other sizes, no inline font sizes.

**The Tabular Rule.** Every figure in a table, tile or chart uses `font-variant-numeric: tabular-nums`.

## Layout

- App shell: 240px sidebar (220px under 1200px; a menu under 900px), sticky top bar with the demo-data band pinned beneath it, content pane with 24px padding (16px under 1200px, 12px under 600px).
- Spacing scale: 4, 8, 12, 16, 24, 32, 48. Inside a card: 16px between items, 24px between groups; between cards: 16px; above a section heading: 32px.
- Page order: status strip, then triage (what needs a person now), then analysis. Analysis never pushes triage below the first screen on a 900px-tall desktop.
- Grids: two columns at 680px+ content width, one below. Wide cards (tables, timelines) span both.
- Ask Chhaya: a 400px right drawer (pushes content at 1280px+, overlays below), expandable to a full overlay with a 920px reading column.
- Field crew pages are a single column, max 1050px.

## Elevation & Depth

Flat by default: depth comes from the cream-to-white step and hairlines.

### Shadow Vocabulary
- **Raised** (`box-shadow: 0 8px 28px rgba(12, 51, 59, 0.10)`): dialogs and the Ask Chhaya drawer only.
- **Overlay** (`box-shadow: 0 24px 64px rgba(12, 51, 59, 0.28)`): the full-view Ask Chhaya overlay.
- **Sticky** (`box-shadow: 0 1px 0 #E4E0D3`): the sticky top bar's bottom edge.

### Named Rules
**The One Elevation Rule.** A surface has a border or a shadow, never both. Cards use a hairline border and no shadow.

## Shapes

- Radii: 6px (small controls, legend chips), 8px (buttons, inputs, table containers), 12px (cards, dialogs), 999px (pills and tags only).
- No coloured accent stripes on rounded shapes (no thick `border-left` / `border-top` accents). Emphasis comes from a tinted background or a pill.
- Icons: line icons, 1.75–2px stroke, 16–20px, `currentColor`. No emoji, no Unicode glyphs as icons.

## Components

### Buttons
- **Shape:** 8px radius, 44px tall, 10px × 18px padding, 14px label text at 600 weight.
- **Primary:** Shade Green fill, white text; hover Deep Shade. One primary per view.
- **Secondary:** white fill, Shade Green text, Control-line border; hover Leaf Wash.
- **Danger:** Red tint fill, Red ink text, red-tint border; used only for "Ask an officer to review" and "Raise to Red".
- **Text link button:** Shade Green, underline offset 3px, 44px target.
- **Focus:** 3px Shade Green outline, 3px offset, on every interactive element.

### Pills and tags
- **Tier pill:** tint background, tier ink text, no border, 12px/500 label, 999px radius, always carries the tier word.
- **Neutral pill:** Sunken background, Ink secondary text. For "Heat scenario", "Simulated call", "Simulated visit", "Test call", counts.
- **Info pill:** Info Blue tint, Info Blue text. Only for "Flagged by worker".
- Pills are status, never buttons. Filter chips are buttons (Leaf Wash, Shade Green border, 44px).

### Cards and containers
- White, 1px Hairline border, 12px radius, 20px padding, no shadow.
- Header: Title (18/600) and an optional Small subtitle in Ink secondary; actions right-aligned.
- Never nest a bordered card inside a card: inner groups use spacing, a Sunken row background, or a hairline divider.

### Status strip (signature)
- The first block of Overview and Urgent cases: one sentence of status ("Red heat warning in Churu · round open since 14:29 · 12 of 40 reached") plus 3–4 figures in Display type with Label captions. Figures that need action use tier ink; others use Ink.
- Before any call is placed, the strip says so plainly and shows who will be called first; it never shows a row of zeros.

### Tables
- Inside a white card; header row on Sunken with Label text and a Strong hairline; rows 44px min, Small text, hairline separators, hover Leaf Wash.
- Numbers right-aligned and tabular. Resident names bold in Ink (not underlined); the whole row opens the case.
- Wide tables become stacked rows under 600px.

### Legends
- Inline, unboxed: a Label "Risk score" or "Tier" followed by items separated by spacing, on the card's own background. Shown wherever a risk score or tier chart appears.

### Inputs and selects
- White, 1px Control-line border, 8px radius, 44px tall, Body text; focus ring as buttons; label above in Label style.

### Navigation
- Sidebar: Label section titles (sentence case, Ink muted); links 44px, Ink secondary, active link Shade Green fill with white text; counters as small Red pills.
- Top bar: warning pill (Red or Amber tint, tier ink), data freshness with Refresh, "Ask Chhaya" secondary button with the sun sparkle.

### Dialog (resident case)
- Native `<dialog>`, 12px radius, Raised shadow, 22px title, two columns (facts 35%, evidence) collapsing to one under 600px.
- Facts as a summary list: Label key, Body value, hairline between rows. Actions grouped at the bottom of the facts column.

## Do's and Don'ts

### Do:
- **Do** lead every screen with its status and the work that needs a person now.
- **Do** write tiers and warning levels as Red, Amber, Green, with the colour, every time.
- **Do** use the data palette for every non-tier series.
- **Do** keep pointer targets at 44px; enlarge a slim bar's hit area with an overlay instead of growing it.
- **Do** use tabular numerals for every figure.
- **Do** show a real empty state that says why something is empty and what happens next.

### Don't:
- **Don't** put white cards inside bordered cards, or a bordered box inside a card.
- **Don't** use amber or green for "no answer", "reached", focus bands or decoration.
- **Don't** use thick coloured stripes on rounded cards, gradients, glass, blur or emoji icons.
- **Don't** set font sizes, colours or spacing inline in components; use the tokens.
- **Don't** use uppercase text or letter-spaced eyebrows.
- **Don't** show a dashboard of zeros; say what state the round is in.
