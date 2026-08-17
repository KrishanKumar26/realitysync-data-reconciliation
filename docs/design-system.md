# Design system

What the interface is built from, and the rules that decide when to use which
piece. Written so a second person can add a screen that looks like it belongs.

---

## The one rule that shapes everything else

**A number on screen must correspond to a number the API returned.** Not a
rounded stand-in, not a zero substituted for a missing value, not a trend line
interpolated between two points.

This is a product whose thesis is that an unverified green light is worse than a
stated unknown. An interface that renders `0%` for "not measured", or draws a
smooth curve through three data points, undermines the thing being sold. So:

- Unavailable measures render as **—**, never `0`.
- Confidence, while its specification is missing, renders as "Confidence
  unavailable" — a phrase, not a blank and not a number.
- **There are no trend charts.** The API exposes counts and compositions
  (`observation_count`, `by_severity`, `syncs_in_window`), not time series.
  Bucketing the truncated activity feed into a line would present a partial
  sample as history. When the API gains a real time series, `chart.tsx` is where
  the chart goes.

---

## Tokens

All in `src/styles/globals.css`. Nothing hard-codes a colour.

| Group | Tokens | Notes |
| --- | --- | --- |
| Surface | `background` `panel` `muted` `border` `border-strong` `ring` `overlay` | Defined for light, redefined for dark twice: under `prefers-color-scheme` and under `[data-theme="dark"]`, so an explicit choice wins in both directions. |
| Status | `status-healthy` `status-degraded` `status-down` `status-unknown` | |
| Confidence | `confidence-high` `-good` `-fair` `-low` | `>=90` cyan, `70-89` blue, `50-69` amber, `<50` red. Fixed product-wide. **No component may invent its own confidence colours** — if two surfaces disagree, the colour stops meaning anything. |
| Brand | `accent-cyan` `accent-violet` | Used for the mark, the active-nav marker and neutral emphasis. Never for status. |
| Elevation | `shadow-surface` `shadow-raised` | Per-theme. A shadow tuned for a white page is invisible on a dark one, so the dark values are deeper. |

Colours are `oklch` so that lightness is perceptually even across hues — an
amber and a red at the same `L` actually look equally bright.

---

## Components

### Layout

| Component | Use for |
| --- | --- |
| `PageHeader` | Every screen's title, description, actions and optional up-link. One component so the title size and action position do not drift between screens. |
| `Panel` / `PanelHeader` / `PanelBody` / `PanelFooter` | Every content section. `PanelHeader` takes an optional decorative `icon`. |
| `MetricGrid` / `Metric` | The headline figures at the top of a screen. Distinct from a figure *inside* a panel: a metric is its own surface with an icon, a tone and a footer. |

### Data

| Component | Use for |
| --- | --- |
| `Table` and friends | Anything genuinely tabular — sync runs, source lists, configured tables. Narrow screens scroll horizontally inside `TableContainer`; there is no second card layout, because a second layout is a second thing to keep correct and a scrolling table keeps its header, alignment and scan lines. |
| `DataView` | Record payloads and values. A flat object of scalars becomes a key/value grid; anything nested keeps a `<pre>`. Values are never reformatted, and `null` renders as the word, distinctly — a null the source stated and a field the source omitted are different facts. |
| `Donut` / `BarRows` / `RatioBar` | Compositions and ratios. Hand-drawn SVG, no charting library: these are arcs and rectangles over small integers. |

### Feedback

| Component | Use for |
| --- | --- |
| `Badge` | Every status, severity and category chip. One component, six tones. `dot` adds a shape as a second channel so colour is never the only carrier of meaning. |
| `EmptyState` / `ErrorState` | Every list and panel must have both. They carry an icon by default — an empty panel of grey text reads as a page that failed to load, where a bordered glyph reads as a place that is deliberately empty. |
| `Skeleton` | Loading. Geometry must match the content it replaces, or the page jumps when data arrives. |
| `ConfirmAction` | Destructive actions. Arms on first press, fires on second, disarms after five seconds. |

### Input

| Component | Use for |
| --- | --- |
| `Button` | `primary` `secondary` `ghost` `danger`. `danger` is for an *armed* destructive action only — a row of resting red buttons trains people to ignore red. |
| `Field` / `Input` | Labelled text input with error and hint slots. |
| `Select` | Labelled dropdown. Native `<select>` deliberately: keyboard-accessible, screen-reader-correct, and the platform picker on a phone — none of which a hand-built listbox gets for free. |

---

## Accessibility rules

Not aspirations; each is currently true and several are asserted by test.

- **Icons are decorative.** Every icon carries `aria-hidden="true"`, so a
  button's accessible name is its text. This is why the shell test can still
  enumerate navigation by label.
- **Colour is never alone.** Status carries a word; `Badge dot` and the
  active-nav bar add shape.
- **Focus is visible everywhere** — a 2px ring at 2px offset, set globally.
- **The mobile sidebar is `invisible` when closed**, not merely translated
  off-screen: an off-screen element stays in the tab order, so a keyboard user
  on a phone would tab through six hidden links before reaching the page.
- **Escape closes the sidebar**, and navigating closes it — a menu left open
  covers the page it just moved to.
- `prefers-reduced-motion` neutralises every animation, globally.

---

## Responsive

One implementation per screen, not a desktop version and a mobile version.

| Breakpoint | Layout |
| --- | --- |
| `< lg` | Sidebar is a slide-over with a backdrop. Panels stack. Tables scroll horizontally. |
| `lg` (1024) | Sidebar becomes a sticky 16rem column. Dashboard splits into 2/3 + 1/3. |
| `xl` (1280) | Metric row goes to four across. |

Content is capped at `max-w-7xl`. The previous `max-w-5xl` left two thirds of a
desktop monitor empty on screens whose purpose is showing operational tables
side by side.

---

## Terminology

Display names are product language; internal names are unchanged. See
[terminology.md](terminology.md) for the full mapping and the list of names
deliberately left alone.
