# Frontend Anti-Pattern Rules

Deterministic quality rules for AI-generated frontend code. Applied by `/kiro:validate-design` and the adversarial agent when reviewing UI components.

Source: [Impeccable](https://github.com/pbakaus/impeccable) — adapted for harness enforcement.

## Enforcement Level

Rules marked **BLOCK** must be fixed before GO. Rules marked **WARN** are flagged but don't gate.

---

## AI Design Fingerprints — BLOCK

These patterns reliably identify AI-generated UIs and must be removed:

| Rule | Pattern | Action |
|---|---|---|
| AP-01 | `background-clip: text` gradient text | Remove — use solid color |
| AP-02 | `backdrop-filter: blur()` glassmorphism | Remove — use solid background |
| AP-03 | `border-left: Npx solid` colored accent border | Remove — use padding/spacing instead |
| AP-04 | `linear-gradient()` on hero/card backgrounds | Remove — use flat color |
| AP-05 | Card nested inside card | Flatten hierarchy |
| AP-06 | 3-col grid of identical height/width cards | Vary layout or use list |
| AP-07 | `background: #ffffff` or `background: white` | Replace with warm off-white (#fafaf8) |

---

## Typography — BLOCK

| Rule | Check | Action |
|---|---|---|
| TY-01 | Body font-size < 14px | Raise to 16px |
| TY-02 | Body line-height < 1.5 | Set to 1.6 |
| TY-03 | More than 2 font families | Consolidate |
| TY-04 | All-caps body text block | Remove text-transform |
| TY-05 | Heading hierarchy skips (h1 → h3) | Fix heading order |

---

## Color & Contrast — BLOCK

| Rule | Check | Action |
|---|---|---|
| CO-01 | Contrast ratio < 4.5:1 for body text | Darken text or lighten bg |
| CO-02 | Contrast ratio < 3:1 for large text / UI | Fix per WCAG AA |
| CO-03 | Gray text on colored background | Always use dark-on-light or light-on-dark |
| CO-04 | More than 1 accent color | Remove competing accents |

---

## Spacing & Surfaces — WARN

| Rule | Check | Action |
|---|---|---|
| SP-01 | Shadow opacity > 0.15 | Reduce (box-shadow rgba alpha) |
| SP-02 | Border-radius > 8px on containers | Reduce to ≤8px |
| SP-03 | Arbitrary spacing values (13px, 17px, 22px) | Align to 4px/8px grid |

---

## Motion — BLOCK

| Rule | Check | Action |
|---|---|---|
| MO-01 | `ease-in` or `ease-out` easing | Replace with `cubic-bezier` or `ease-in-out` |
| MO-02 | Micro-interaction duration > 200ms | Cap at 150–200ms |
| MO-03 | Missing `prefers-reduced-motion` media query | Add `@media (prefers-reduced-motion: reduce)` |

---

## Interaction & Accessibility — BLOCK

| Rule | Check | Action |
|---|---|---|
| IA-01 | Missing `:focus-visible` styles | Add explicit focus ring |
| IA-02 | Icon-only button without `aria-label` | Add aria-label |
| IA-03 | List/table component missing empty state | Design empty state |
| IA-04 | Async action missing loading state | Add loading indicator |
| IA-05 | Error message is generic ("An error occurred") | Make specific and actionable |

---

## UX Writing — WARN

| Rule | Check | Action |
|---|---|---|
| UW-01 | Filler text ("Lorem ipsum") | Replace with real copy |
| UW-02 | Button label is not a verb | Rewrite ("Save changes", not "Submit") |
| UW-03 | Empty state has no call-to-action | Add guidance copy |

---

## Usage in Validation

When `/kiro:validate-design` or the adversarial agent encounters UI components, reference these rules as the deterministic enforcement layer for visual quality. BLOCK items must resolve before design approval. WARN items should be documented in the design review output.
