# ELIZA Project and Client Management App

A Flask web application for managing clients, projects, tasks, subscriptions and billing.
Deployed on Vercel with a Neon (PostgreSQL) database.

## Features

- **Authentication** — register, login, logout, password reset (request + token-based reset)
- **Client management** — create, view, edit, delete clients
- **Project management** — create, view, edit, delete projects; assign/remove team members
- **Task management** — create, view, edit, delete tasks; comments on tasks; time tracking (start/stop/manual entry) per task
- **Reports & analytics** — project status, task distribution, time tracking, project timeline, overdue tasks; each report can be exported (chart images rendered with matplotlib, data via pandas)
- **Subscriptions & billing** — subscription plans, trial sign-up, payment via **PesaPal**, payment callback handling, cancel subscription, subscription status
- **Email** — bulk email sending, unsubscribe, automatic notifications for task assignment, task updates, and task comments
- **Admin panel** — admin dashboard, user management (add/edit/delete), subscription plan management, system settings
- **CSRF protection** on all forms (Flask-WTF)

> `security.py` also implements HTTPS-forcing security headers via Flask-Talisman (CSP, HSTS, secure cookies, clickjacking/MIME-sniffing protection), but it isn't wired into `api/index.py` yet — worth enabling before a public production launch.

## Tech Stack

- **Flask** — web framework
- **Flask-SQLAlchemy** — ORM
- **PostgreSQL** (Neon) via `psycopg2-binary` — production database (SQLite fallback for local dev)
- **Flask-Login** — session/auth management
- **Flask-WTF / WTForms** — forms and CSRF protection
- **Flask-Mail** — transactional and notification email
- **PesaPal** — payment gateway for subscriptions
- **pandas / matplotlib** — report data processing and chart generation
- **PyJWT** — token handling (e.g. password reset)
- **Vercel** — hosting (`api/index.py` is the serverless entry point)

## Project Structure

```
ELIZA_App/
├── api/
│   ├── index.py          # Main Flask app + most routes (auth, clients, projects, tasks, reports, admin)
│   ├── main.py            # Landing/home routes
│   ├── subscription.py    # Subscription plan + PesaPal payment routes
│   ├── payment.py         # PesaPal integration
│   ├── admin.py           # Admin panel routes
│   └── email.py           # Bulk email + unsubscribe routes
├── models/
│   ├── models.py          # User, Client, Project, Task, Comment, TimeEntry, ProjectMember, ActivityLog
│   └── subscription.py    # Subscription, SubscriptionPlan, Payment
├── services/               # Business logic services
├── utils/
│   ├── db_utils.py        # DB pool config, retry/safe query helpers
│   └── email_utils.py     # Email sending + notification templates
├── forms.py                # WTForms form definitions
├── filters.py               # Custom Jinja filters
├── security.py              # Talisman security headers (not yet wired in)
├── config.py                 # App configuration
├── migrations/               # Manual DB migration scripts
├── templates/                # Jinja templates (auth, clients, projects, tasks, reports, subscription, admin, emails)
├── static/                    # CSS, images, robots.txt, sitemap.xml
├── vercel.json                 # Vercel build/routing config
└── requirements.txt
```

## Setup for Local Development

1. **Navigate to the project directory**:
   ```bash
   cd path\to\ELIZA_App
   ```

2. **Create and activate a virtual environment** (recommended):
   ```bash
   python -m venv venv

   # Windows (PowerShell):
   .\venv\Scripts\Activate.ps1
   # Linux/macOS:
   source venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Set up environment variables**:
   Copy `.env.example` to `.env` and fill in real values:
   ```env
   DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require
   SECRET_KEY=your_secret_key
   FLASK_ENV=development
   MAIL_SERVER=...
   MAIL_PORT=587
   MAIL_USE_TLS=True
   MAIL_USERNAME=...
   MAIL_PASSWORD=...
   MAIL_DEFAULT_SENDER=...
   PESAPAL_CONSUMER_KEY=...
   PESAPAL_CONSUMER_SECRET=...
   PESAPAL_IPN_ID=...
   ```
   If `DATABASE_URL` is not set, the app falls back to a local SQLite file (`eliza.db`) — fine for local development, but production must set a real Postgres URL.

   **Never commit your real `.env` file** — it's already git-ignored.

5. **Run the app**:
   ```bash
   python api/index.py
   ```
   This creates any missing tables on startup and serves at `http://127.0.0.1:5000/`.

## Deployment to Vercel

- Connect your Git repository (GitHub) to Vercel.
- Set `DATABASE_URL`, `SECRET_KEY`, and the mail/PesaPal variables above as Environment Variables in the Vercel project settings.
- Vercel uses `vercel.json` for build/routing config and `api/index.py` as the serverless function entry point.
- Static files under `static/` are served automatically per `vercel.json`.

## Known Gaps

- `security.py`'s Talisman security headers are written but not yet applied in `api/index.py`.
- No automated test suite yet.
