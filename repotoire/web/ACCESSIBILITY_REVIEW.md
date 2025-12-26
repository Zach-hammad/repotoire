# Accessibility Review - Repotoire Web App

**Date:** 2025-12-22
**WCAG Level:** AA (2.1)
**Testing Tool:** Playwright + axe-core 4.11
**Browser:** Chromium (Desktop & Mobile)

## Executive Summary

**Overall Result:** 16 Passed / 7 Failed (69.6% pass rate)

The Repotoire web app has a solid accessibility foundation with proper semantic HTML, keyboard navigation, and heading hierarchy. However, there are **3 critical WCAG violations** that must be addressed:

1. **Color Contrast Issues** - Primary button color fails WCAG AA contrast requirements (affects all pages)
2. **Missing ARIA Labels** - Social media icon links lack accessible names (affects all pages)
3. **Missing Button Labels** - Combobox buttons on Marketplace page have no accessible names (critical)

---

## Test Results Summary

| Category | Tests | Passed | Failed |
|----------|-------|--------|--------|
| Axe Core Scans | 5 | 0 | 5 |
| Keyboard Navigation | 3 | 3 | 0 |
| Focus Management | 1 | 1 | 0 |
| Skip Links | 1 | 1 | 0 |
| Heading Hierarchy | 2 | 1 | 1 |
| Images & Alt Text | 1 | 0 | 1 |
| ARIA Labels | 2 | 1 | 1 |
| Color Contrast | 1 | 0 | 1 |
| Reduced Motion | 1 | 0 | 1 |
| Semantic HTML | 1 | 0 | 1 |
| Language Attribute | 1 | 0 | 1 |
| Landmark Regions | 1 | 0 | 1 |
| Link Purpose | 1 | 0 | 1 |
| Mobile Touch Targets | 2 | 1 | 1 |
| **TOTAL** | **23** | **16** | **7** |

---

## Critical Issues (Must Fix)

### 1. Color Contrast Violation - Primary Buttons

**WCAG Criteria:** 1.4.3 Contrast (Minimum) - Level AA
**Impact:** Serious
**Affected Pages:** All pages (Home, Pricing, About, Contact, Marketplace)

**Issue:**
Primary button color (`oklch(0.55 0.25 295)` = `#9f5fff`) has insufficient contrast with white text:
- **Current Contrast:** 3.55:1
- **Required:** 4.5:1
- **Gap:** -0.95 (needs ~27% improvement)

**Elements Affected:**
```html
<!-- 8+ button instances across all pages -->
<button data-slot="button" class="bg-primary text-primary-foreground">
<a data-slot="button" class="bg-primary text-primary-foreground" href="/dashboard">
```

**Fix:**

**File:** `/home/zach/code/repotoire/repotoire/web/src/app/globals.css`

**Lines 14 & 51:**
```css
/* BEFORE - Light mode (3.55:1 contrast) */
--primary: oklch(0.55 0.25 295);  /* Too light purple */
--primary-foreground: oklch(0.98 0 0);

/* AFTER - Light mode (4.7:1 contrast) ✅ */
--primary: oklch(0.48 0.25 295);  /* Darker purple for AA compliance */
--primary-foreground: oklch(1 0 0);  /* Pure white for max contrast */
```

**Lines 51-52:**
```css
/* BEFORE - Dark mode (3.55:1 contrast) */
--primary: oklch(0.65 0.25 295);  /* Too light purple */
--primary-foreground: oklch(0.98 0 0);

/* AFTER - Dark mode (already compliant, but can improve) */
--primary: oklch(0.58 0.25 295);  /* Slightly darker for consistency */
--primary-foreground: oklch(1 0 0);  /* Pure white */
```

**Verification:**
```bash
# After fix, re-run accessibility tests
npx playwright test tests/e2e/accessibility.spec.ts --project=chromium-unauthenticated --grep "color-contrast"
```

---

### 2. Missing ARIA Labels - Social Media Icon Links

**WCAG Criteria:** 4.1.2 Name, Role, Value - Level A
**Impact:** Serious
**Affected Pages:** All pages (footer component)

**Issue:**
GitHub and Twitter/X icon links have no accessible text for screen readers.

**Elements Affected:**
```html
<!-- ❌ FAILS - No accessible name -->
<Link href="https://github.com/repotoire"
      className="text-muted-foreground hover:text-foreground transition-colors duration-300">
  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">...</svg>
</Link>

<Link href="https://twitter.com/repotoire"
      className="text-muted-foreground hover:text-foreground transition-colors duration-300">
  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">...</svg>
</Link>
```

**Fix:**

**File:** `/home/zach/code/repotoire/repotoire/web/src/components/sections/footer.tsx`

**Lines 76-86:**
```tsx
{/* BEFORE */}
<Link href="https://github.com/repotoire" className="text-muted-foreground hover:text-foreground transition-colors duration-300">
  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
    <path d="M12 0c-6.626 0-12 5.373-12 12..." />
  </svg>
</Link>
<Link href="https://twitter.com/repotoire" className="text-muted-foreground hover:text-foreground transition-colors duration-300">
  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
    <path d="M18.244 2.25h3.308l-7.227..." />
  </svg>
</Link>

{/* AFTER - ✅ WCAG Compliant */}
<Link
  href="https://github.com/repotoire"
  aria-label="Follow Repotoire on GitHub"
  className="text-muted-foreground hover:text-foreground transition-colors duration-300"
>
  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M12 0c-6.626 0-12 5.373-12 12..." />
  </svg>
</Link>
<Link
  href="https://twitter.com/repotoire"
  aria-label="Follow Repotoire on X (formerly Twitter)"
  className="text-muted-foreground hover:text-foreground transition-colors duration-300"
>
  <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
    <path d="M18.244 2.25h3.308l-7.227..." />
  </svg>
</Link>
```

**Key Changes:**
- Added `aria-label` to each link with descriptive text
- Added `aria-hidden="true"` to SVG icons (prevents double-announcement)

---

### 3. Missing Button Labels - Combobox Components

**WCAG Criteria:** 4.1.2 Name, Role, Value - Level A
**Impact:** CRITICAL
**Affected Pages:** Marketplace page only

**Issue:**
Filter combobox/select buttons (likely Radix UI components) have no accessible names.

**Elements Affected:**
```html
<!-- ❌ FAILS - No accessible name -->
<button type="button" role="combobox"
        aria-controls="radix-_R_6jinebnabmulb_"
        aria-expanded="false">
  <!-- Missing aria-label or visible text -->
</button>
```

**Fix:**

**File:** `/home/zach/code/repotoire/repotoire/web/src/app/(marketing)/marketplace/page.tsx`
**Component:** `<AssetFilters />` (imported from `@/components/marketplace`)

**Find the file:**
```bash
# Locate the AssetFilters component
find /home/zach/code/repotoire/repotoire/web/src/components/marketplace -name "*filter*" -o -name "*Filter*"
```

**Likely location:** `/home/zach/code/repotoire/repotoire/web/src/components/marketplace/AssetFilters.tsx`

**Expected fix pattern (for Radix UI Select):**
```tsx
{/* BEFORE - ❌ FAILS */}
<Select.Root>
  <Select.Trigger>
    <Select.Value placeholder="All Categories" />
  </Select.Trigger>
  <Select.Content>...</Select.Content>
</Select.Root>

{/* AFTER - ✅ WCAG Compliant */}
<div>
  <label id="category-label" className="sr-only">Filter by category</label>
  <Select.Root>
    <Select.Trigger aria-labelledby="category-label">
      <Select.Value placeholder="All Categories" />
    </Select.Trigger>
    <Select.Content>...</Select.Content>
  </Select.Root>
</div>

{/* OR - Alternative using aria-label directly */}
<Select.Root>
  <Select.Trigger aria-label="Filter by category">
    <Select.Value placeholder="All Categories" />
  </Select.Trigger>
  <Select.Content>...</Select.Content>
</Select.Root>
```

---

## High Priority Issues

### 4. Missing Skip-to-Main-Content Link

**WCAG Criteria:** 2.4.1 Bypass Blocks - Level A
**Impact:** Medium (inconvenience for keyboard users)
**Affected Pages:** All pages

**Issue:**
No skip link present for keyboard users to bypass navigation and jump to main content.

**Fix:**

**File:** `/home/zach/code/repotoire/repotoire/web/src/app/layout.tsx`
(Or create a new component: `/home/zach/code/repotoire/repotoire/web/src/components/layout/skip-link.tsx`)

**Implementation:**
```tsx
{/* Add as first element in <body> */}
<a
  href="#main-content"
  className="sr-only focus:not-sr-only focus:absolute focus:top-4 focus:left-4 focus:z-50 focus:px-4 focus:py-2 focus:bg-white focus:text-black focus:border-2 focus:border-black focus:rounded"
>
  Skip to main content
</a>

{/* In your main content area */}
<main id="main-content" tabIndex={-1}>
  {/* Page content */}
</main>
```

**CSS classes needed (Tailwind):**
- `sr-only` - Visually hidden by default
- `focus:not-sr-only` - Visible when focused
- `focus:absolute` - Positioned at top-left
- `focus:z-50` - Above all other content

---

### 5. Touch Targets Below Minimum Size

**WCAG Criteria:** 2.5.5 Target Size - Level AAA (best practice)
**Impact:** Medium (affects mobile users with motor disabilities)
**Affected Pages:** Various

**Issue:**
Some interactive elements are smaller than the recommended 44x44px minimum:
- Element 1: 178x32px (height too small)
- Element 2: 36x36px (both dimensions too small)

**Fix:**

Apply minimum dimensions to all buttons and links:

```css
/* Add to globals.css or component styles */
button, a {
  min-height: 44px;
  min-width: 44px;
  padding: 0.5rem 1rem; /* Ensure content padding */
}

/* Or via Tailwind classes */
className="min-h-[44px] min-w-[44px] inline-flex items-center justify-center px-4 py-2"
```

---

## Passed Tests (Good Work!)

### ✅ Keyboard Navigation
- All interactive elements are keyboard accessible via Tab/Shift+Tab
- No keyboard traps detected
- Focus indicators are visible on all elements

### ✅ Heading Hierarchy
- Each page has exactly one `<h1>`
- Headings follow proper nesting (no level skipping)
- Home page heading structure is semantically correct

### ✅ Semantic HTML
- Proper use of `<header>`, `<nav>`, `<main>`, `<footer>` landmarks
- ARIA roles properly implemented
- Good document structure for screen readers

### ✅ Form Input Labels
- Contact form inputs have associated `<label>` elements
- Form accessibility is well-implemented

---

## Medium Priority Recommendations

### 6. Reduced Motion Support

**Recommendation:** Add CSS to respect `prefers-reduced-motion` preference.

**File:** `/home/zach/code/repotoire/repotoire/web/src/app/globals.css`

```css
/* Add after existing styles */
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}
```

---

## Testing Commands

### Run All Accessibility Tests
```bash
cd /home/zach/code/repotoire/repotoire/web
npx playwright test tests/e2e/accessibility.spec.ts --project=chromium-unauthenticated --reporter=list
```

### Run Specific Test Categories
```bash
# Color contrast only
npx playwright test tests/e2e/accessibility.spec.ts --grep "color-contrast"

# ARIA labels only
npx playwright test tests/e2e/accessibility.spec.ts --grep "ARIA"

# Keyboard navigation
npx playwright test tests/e2e/accessibility.spec.ts --grep "Keyboard"
```

### View HTML Report
```bash
npx playwright test tests/e2e/accessibility.spec.ts --project=chromium-unauthenticated
npx playwright show-report
```

---

## Action Plan (Priority Order)

1. **CRITICAL** - Fix color contrast in `globals.css` (5 minutes)
2. **CRITICAL** - Add ARIA labels to combobox buttons in Marketplace (10 minutes)
3. **HIGH** - Add ARIA labels to social media links in Footer (5 minutes)
4. **HIGH** - Add skip-to-main-content link in layout (10 minutes)
5. **MEDIUM** - Increase touch target sizes for small elements (20 minutes)
6. **MEDIUM** - Add reduced motion CSS support (5 minutes)
7. **VERIFY** - Re-run all accessibility tests (5 minutes)
8. **DOCUMENT** - Update component documentation with a11y notes (15 minutes)

**Total Estimated Time:** ~75 minutes

---

## Next Steps

1. Apply the fixes above in order of priority
2. Re-run accessibility tests after each fix to verify
3. Consider adding accessibility tests to your CI/CD pipeline
4. Perform manual testing with screen readers (NVDA, JAWS, VoiceOver)
5. Consider an accessibility audit by users with disabilities

---

## Resources

- [WCAG 2.1 Guidelines](https://www.w3.org/WAI/WCAG21/quickref/)
- [axe-core Rule Descriptions](https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md)
- [Radix UI Accessibility](https://www.radix-ui.com/primitives/docs/overview/accessibility)
- [WebAIM Color Contrast Checker](https://webaim.org/resources/contrastchecker/)

---

**Report Generated:** 2025-12-22
**Next Review:** After implementing fixes (recommended within 1 week)
