# SurgeSignal design system

Editorial and evidence-first, in the spirit of a data story in the FT or The Pudding rather than a SaaS dashboard. The audience is an Economics judge with two minutes, and a dispatcher glancing at a phone. Decided in /plan-design-review on 2026-09-24.

## Principles

1. **Claim, then action, then proof, then method.** Every screen opens with what's true and what to do, and only then shows how we know.
2. **One accent means one thing.** Red is demand. It is never used for decoration, links or the brand's own sake.
3. **Every number is labelled** EVIDENCE (with a source) or ESTIMATE (an assumption). Nothing is shown as fact that isn't.
4. **Cardless by default.** Only three components earn a container: the action card, the phone alert and the figure strip.
5. **No TfL identity.** No roundel, no Johnston typeface, no TfL blue. SurgeSignal is not endorsed by TfL, and nothing should suggest it is.
6. **No emoji as design elements.** The only emoji allowed are ⚠️ ℹ️ ✅ inside the rendered Telegram alert, because that's what the real message contains.

## Tokens

```
--bg          #FAFAF7   warm off-white page
--surface     #F0EFEA   figure strip, expanders, table header
--ink         #14161A   text, disrupted-section line, wordmark
--muted       #5B5F66   captions, secondary text          (6.1:1 on --bg)
--rule        #E2E0D8   hairlines
--accent      #C8321E   = demand (top of the scale); primary buttons   (5.1:1 on --bg; white on it 5.3:1)
--estimate    #6F5712   ESTIMATE badge text on #F6EEDA   (6.0:1)
--evidence    #1F5E4A   EVIDENCE badge text on #E3F0EA   (6.5:1)
--stale       #B42318   stale/error banner text on #FDECEA (5.8:1)
--phone       #E7EBF0   Telegram chat background inside the phone frame
```

Demand scale (sequential, one hue, readable in greyscale), from 0% to ≥ the frame's maximum:
`#FBEFE6 → #F4C7A8 → #E8895F → #C8321E → #7A1A0E`

## Type

- **IBM Plex Serif** 600: the H1 claim and section headlines only.
- **IBM Plex Sans** 400/500/600: everything else, with tabular figures (`font-variant-numeric: tabular-nums`) for every number.
- Scale: H1 40/48, H2 26/32, H3 19/26, body 16/26, small 14/20. Body never below 16px.
- Loaded from Google Fonts with fallbacks `Georgia, serif` and `Helvetica Neue, Arial, sans-serif`.

## Space and shape

- Spacing 4 / 8 / 12 / 16 / 24 / 32 / 48 / 64. Sections are separated by 48 or 64.
- Radius: 6px everywhere, 28px for the phone frame only.
- No shadows, except one soft shadow on the phone frame (`0 12px 32px rgba(20,22,26,.12)`), because a phone is a physical object.

## Components

- **Wordmark:** "SurgeSignal" in Plex Serif 600, ink, plus a small red demand dot. No emoji, no taxi.
- **Figure strip:** three figures on a single `--surface` band, never three cards. Each has a 26px Plex Sans 600 number, a 14px label and a badge.
- **Badges:** `EVIDENCE` / `ESTIMATE` / `REPLAY · recorded 23 Sep 19:43` / `LIVE · checked 40 s ago`. Uppercase 12px, letter-spacing .04em, radius 4px.
- **Action card:** one large number ("Move 2 of 6 idle cars", 32px 600), then the stations with their car split, the "until ~19:35" window, and the one-line why. 1px `--rule` border, 6px radius.
- **Phone alert:** a 300×560 frame containing a Telegram-style bubble (white, 12px radius, 14px Plex Sans) with the exact text the bot sends. Stacks above the map on narrow screens.
- **Maps:** carto-positron basemap. The disrupted section is drawn as a 4px `--ink` line with small ink station markers. Demand circles use the demand scale, with size = riders added. The area is a thin grey (#8A8E95) outline (Plotly map lines cannot be dashed). The four ripple frames share one colour scale and one zoom.
- **Tables:** `st.dataframe` with `column_config`. % ranges are shown as bars in the demand colour. Evidence and estimate are badge columns.

## Motion

None decorative. The ripple is shown as four small frames side by side (small multiples) instead of an animation, so it works in a still screenshot, a video and on a skim.

## Responsive and accessibility

- Designed for a 1440px desktop and a 1920×1080 video capture. Streamlit stacks columns below about 640px: the four ripple frames become a vertical sequence, and the phone moves above the map.
- Colour is never the only channel: every map has a caption naming the top stations and their %.
- Contrast is at least 4.5:1 for all text; each token's ratio is noted next to it (computed with the WCAG formula).
- Controls are native Streamlit widgets and pills, so keyboard focus comes free. Nothing is required in the sidebar (there is no sidebar).
