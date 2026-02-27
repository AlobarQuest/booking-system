# Mobile Layout Design — Booking Page

**Date:** 2026-02-27

## Problem

On mobile screens (~375px wide), the appointment type cards use a horizontal flex layout with a 170px-wide photo on the right. This leaves only ~165px for all text content, making cards feel cramped and hard to read. Additional pain points: the header has generous desktop padding that wastes screen space on mobile, and the date picker is artificially capped at 220px width.

## Chosen Approach: Stacked Photo Card

On mobile (≤640px), photos move from the right side of the card to the top, becoming a full-width banner. Text, duration, action buttons, and the Schedule Tour button stack cleanly below. Desktop layout is completely unchanged.

## Changes

### `app/templates/booking/index.html`

Replace inline `style="display:flex;gap:1rem;align-items:flex-start;"` on the card content wrapper div with `class="card-content"`. Remove inline styles that will be handled by the class. Keep all other markup (image, buttons, etc.) unchanged.

Note: the `cloneNode(true)` call that powers the selected-type banner copies class attributes along with the element, so the banner will pick up the new styles automatically.

### `app/static/css/style.css`

**New rule** — desktop base for `.card-content`:
```css
.card-content {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
}
.card-content img {
  width: 170px;
  height: auto;
  border-radius: 8px;
  flex-shrink: 0;
}
.card-content .card-text {
  flex: 1;
  min-width: 0;
}
```

**Mobile overrides** inside `@media (max-width: 640px)`:
```css
.card-content { flex-direction: column-reverse; gap: .75rem; }
.card-content img { width: 100%; height: 140px; object-fit: cover; border-radius: 6px; }
.booking-header { padding: 1.25rem 1rem 2rem; }
#date-input { max-width: 100%; }
```

`flex-direction: column-reverse` is used so that the image (last in DOM) appears visually at the top on mobile, without reordering the HTML.

## Scope

- Only affects the public booking page (`/book`)
- Admin pages unchanged
- Desktop layout (>640px) unchanged
- No JavaScript changes
- No template structural changes beyond adding one CSS class
