# FitKeep — Gym Retention & Attendance SaaS

FitKeep helps gym owners manage members, record attendance, follow up with inactive members, and track memberships and payments. The application is built with Django, SQLite, Django Templates, Bootstrap, and Chart.js.

## Main features

- Gym owner and member portals with role-aware navigation.
- Gym-scoped member and subscription records.
- Enquiry tracking with manual follow-up dates, comments, and click-to-chat WhatsApp messages.
- Attendance check-in, gym-specific QR codes, and daily duplicate prevention.
- Attendance records include an expected end time; live occupancy counts visits only until that time.
- Membership periods for the same member cannot overlap; back-to-back renewals remain valid.
- Inactivity risk calculation using each gym's configured thresholds.
- Contact logs and follow-up tracking for retention calls.
- Membership plans, subscription renewals, payment records, and outstanding balances.
- Member attendance streaks, monthly progress, and achievements.
- Responsive dashboard, attendance, retention, payment, and platform-admin pages.
- Time-limited password-reset links using Django's token generator.

## Local setup

Use Python 3.10+ (Python 3.12 is a good choice for the pinned dependencies).

1. Create and activate a virtual environment.

   ```bash
   python -m venv .venv
   # Windows PowerShell
   .venv\Scripts\Activate.ps1
   # macOS/Linux
   source .venv/bin/activate
   ```

2. Install dependencies.

   ```bash
   pip install -r requirements.txt
   ```

3. Copy `.env.example` to `.env`. Set a unique `SECRET_KEY` before deploying. For local development, the console email backend prints password-reset emails in the terminal.

4. Apply migrations and create a platform admin account.

   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

5. Optionally create demo data, then start Django.

   ```bash
   python manage.py seed_demo_data
   python manage.py runserver
   ```

Open `http://127.0.0.1:8000/`.

## Important workflows

- Register a gym at `/register/`, then sign in as its owner.
- Create your membership plans once under **Plans**.
- Add a member from **Members → Add member**: details, plan, dates, discount and the first payment are all captured in a single form (the end date and fee fill in automatically from the plan). The member's portal login is created at the same time.
- To sell another plan later, use **Assign membership / New membership** on the member's page; use **Record payment** there to collect any balance.
- **Renew** a membership and collect the fee in one step (member page, member list, memberships list or the dashboard's "Renewals due this week").
- Use the search box and **Quick add** menu in the top bar from any page. The member list can be filtered by "expiring in 7 days", "fees due", "expired" and "no membership yet", and each row has one-click *Mark present*, *Renew* and *Record payment* buttons.
- Membership statuses update themselves (finished memberships expire, future ones activate on their start date) - no manual step or cron job is needed.
- Plans can be edited (price, duration, active/inactive) from **Plans**. Existing memberships keep the price they were sold at.
- Lost a member's portal password? Use **Reset portal password** on the member page.
- Generate the gym's attendance QR code from **Attendance**. The QR URL contains a revocable gym-specific token and requires the member to sign in before recording attendance.
- Use **Retention** to review inactivity alerts and record contact outcomes.
- Link payments to the relevant subscription so the outstanding balance stays accurate.
- Configure a real email provider before relying on password-reset emails in production.

## Management commands

- `python manage.py seed_demo_data` — create demo records for local development.
- `python manage.py process_gym_alerts` — refresh inactivity risk and process expiry alerts. Risk notifications are created when a member enters a new risk level; run this periodically with a scheduler for unattended updates. Risk levels use the gym's configured thresholds.

## Tests

Run the Django test suite with:

```bash
python manage.py test
```

The source should also be reviewed and tested in the target Python/Django environment before production deployment. A successful Python syntax compilation alone does not replace Django's system checks, migrations, and integration tests.

## Deployment notes

- Set `DEBUG=False`, a strong unique `SECRET_KEY`, and the correct `ALLOWED_HOSTS`.
- Configure HTTPS, a production email backend, database backups, and a scheduler for alert processing.
- SQLite is suitable for a local MVP. Consider PostgreSQL before running a larger production workload.
- Review access permissions and tenant isolation tests before onboarding real gyms.
