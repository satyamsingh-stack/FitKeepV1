# FitKeep optimization notes

## UI improvements

- Introduced a consistent FitKeep visual system with responsive navigation, typography, spacing, buttons, status badges, forms, cards, tables, empty states, and mobile breakpoints.
- Reworked the public landing page, login and registration pages, owner dashboard, member dashboard, attendance workflow, QR display, retention list/report, payment forms, membership form, calendar, audit history, and platform admin view.
- Replaced placeholder QR, quick check-in, contact history, fee summary, audit, and platform dashboard pages with usable interfaces.
- Removed hardcoded attendance chart values and now build dashboard charts from database aggregates.

## Logic and reliability improvements

- Added gym-local calendar date handling for attendance and streaks.
- Added concurrent duplicate-check-in handling and validation that a member belongs to the gym and is active.
- Added active, revocable QR-token checks for member self-check-in; QR scanning requires an authenticated member account.
- Restricted admin-only data views to gym owners/staff and made notification read actions POST-only.
- Added a safe same-host check for post-login redirects and POST-only logout.
- Added tokenized password reset links and password validation.
- Made retention risk calculation use gym-specific thresholds when custom risk levels are not configured, and exclude members whose recorded subscriptions are no longer valid.
- Calculated retention returns from attendance recorded after contact rather than trusting a manually selected “returned” outcome.
- Fixed membership pricing/discount calculations, renewal date ranges, payment-to-subscription association, and outstanding-balance calculations.
- Replaced a shared default member password with a unique temporary password shown once to the gym owner after member creation.

## Validation note

Python source files were checked with `python -m compileall`. The Django test suite could not be run in the editing environment because Django was not installed and package installation was unavailable. Run `python manage.py check`, `python manage.py migrate`, and `python manage.py test` in an environment with the requirements installed before deploying.
