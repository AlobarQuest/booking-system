# Mobile Layout Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the cramped mobile layout on the public booking page by stacking card photos above content on small screens.

**Architecture:** Two changes only — add a CSS class to the card-content wrapper div in the template (replacing inline styles), then define that class in the stylesheet with a mobile media-query override that switches from horizontal to vertical layout. Desktop layout is completely untouched.

**Tech Stack:** Jinja2 template, CSS media query, pytest

---

### Task 1: Add `card-content` class to template, write and pass a test for it

**Files:**
- Modify: `app/templates/booking/index.html:24-52`
- Test: `tests/test_booking_page.py`

**Step 1: Write the failing test**

Add to `tests/test_booking_page.py` after the existing `test_booking_page_shows_photo` test:

```python
def test_booking_page_card_content_has_class(booking_client):
    resp = booking_client.get("/")
    assert resp.status_code == 200
    assert 'class="card-content"' in resp.text
    assert 'class="card-text"' in resp.text
```

**Step 2: Run test to verify it fails**

```bash
pytest tests/test_booking_page.py::test_booking_page_card_content_has_class -v
```

Expected: FAIL — neither class exists in the template yet.

**Step 3: Update the template**

In `app/templates/booking/index.html`, make these three changes:

**Line 24** — replace inline style with class on the flex wrapper:
```html
    <div id="card-content-{{ appt.id }}" class="card-content">
```
(was: `style="display:flex;gap:1rem;align-items:flex-start;"`)

**Line 25** — replace inline style with class on the text wrapper:
```html
      <div class="card-text">
```
(was: `style="flex:1;min-width:0;"`)

**Lines 50-51** — remove inline styles from the image (CSS will handle them):
```html
      <img src="/uploads/{{ appt.photo_filename }}" alt="{{ appt.name }}">
```
(was: `style="width:170px;height:auto;border-radius:8px;flex-shrink:0;"`)

**Step 4: Run test to verify it passes**

```bash
pytest tests/test_booking_page.py -v
```

Expected: all 4 existing tests + the new test PASS.

**Step 5: Commit**

```bash
git add app/templates/booking/index.html tests/test_booking_page.py
git commit -m "feat: add card-content class to booking card wrapper"
```

---

### Task 2: Add CSS rules and mobile overrides

**Files:**
- Modify: `app/static/css/style.css:104` (after card rules block) and `:218` (inside media query)

**Step 1: Add desktop base rules**

In `app/static/css/style.css`, after the `.card .card-duration { ... }` block (around line 104), insert:

```css
/* Card content layout (desktop: side-by-side, mobile: stacked) */
.card-content { display: flex; gap: 1rem; align-items: flex-start; }
.card-text { flex: 1; min-width: 0; }
.card-content img { width: 170px; height: auto; border-radius: 8px; flex-shrink: 0; }
```

This reproduces exactly what the removed inline styles were doing — so desktop is visually identical.

**Step 2: Add mobile overrides**

Inside the existing `@media (max-width: 640px)` block (currently at the bottom of style.css), add:

```css
  .card-content { flex-direction: column-reverse; gap: .75rem; }
  .card-content img { width: 100%; height: 140px; object-fit: cover; border-radius: 6px; }
  .booking-header { padding: 1.25rem 1rem 2rem; }
  #date-input { max-width: 100%; }
```

`column-reverse` makes the image (last in the DOM) appear visually first (at the top of the card) on mobile, without changing the HTML order.

`#date-input { max-width: 100%; }` overrides the `max-width: 220px` in the base rule, letting the date picker use the full card width on mobile.

The `@media` block will look like:
```css
@media (max-width: 640px) {
  .booking-header { padding: 1.25rem 1rem 2rem; }
  .booking-body { padding: 0 .75rem; }
  .slots-grid { grid-template-columns: repeat(3, 1fr); }
  .admin-container { padding: .75rem; }
  .admin-nav { gap: 1rem; }
  .card-content { flex-direction: column-reverse; gap: .75rem; }
  .card-content img { width: 100%; height: 140px; object-fit: cover; border-radius: 6px; }
  #date-input { max-width: 100%; }
}
```

**Step 3: Run full test suite**

```bash
pytest -v
```

Expected: all 103 tests PASS (CSS changes have no effect on existing HTML-content tests).

**Step 4: Visual check**

Open http://localhost:8080/book in a browser. Use DevTools → Toggle Device Toolbar (or resize to ~390px wide) and confirm:
- Cards with photos: photo appears as a full-width banner at the top of the card
- Cards without photos: title/description layout unchanged
- Desktop (wider than 640px): layout is identical to before — photo on the right at 170px width
- Header is less tall on mobile
- Date picker (after selecting a type and date) spans full width on mobile

**Step 5: Commit**

```bash
git add app/static/css/style.css
git commit -m "feat: stack card photos on mobile, tighten header and date picker"
```

---

### Task 3: Push and sync preview

```bash
git push origin master
git branch -f preview master && git push origin preview --force
```

Preview environment at https://preview.booking.devonwatkins.com will auto-deploy for testing on a real device.
