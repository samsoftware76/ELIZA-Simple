# ELIZA Project and Client Management App

A Flask web application for managing clients, projects, tasks, subscriptions and billing.
Deployed on Vercel with a Neon (PostgreSQL) database.

## Features

- **Authentication** — register, login, logout, password reset (request + token-based reset)
- **Client management** — create, view, edit, delete clients
- **Project management** — create, view, edit, delete projects; assign/remove team members
- **Task management** — create, view, edit, delete tasks; comments on tasks; time tracking (start/stop/manual entry) per task
- **Quotes** — create quotes with line items, send to a client, client accepts/declines via a tokenized no-login portal link, convert an accepted quote into a draft invoice
- **Invoices** — create invoices with line items (or from a converted quote), send to a client, client pays via **PesaPal** through the same portal link, or mark paid manually for cash/bank-transfer payments
- **Client portal** (`/portal/quote/<token>`, `/portal/invoice/<token>`) — no login required; the client reaches it via an emailed, unguessable link. Payment confirmation is looked up by that link's own token, not by session, so it works even if the client completes payment on a different device
- **Reports & analytics** — project status, task distribution, time tracking, project timeline, overdue tasks; each report can be exported (chart images rendered with matplotlib, data via pandas)
- **Subscriptions & billing** — subscription plans, trial sign-up, payment via **PesaPal**, payment callback handling, cancel subscription, subscription status
- **Email** — bulk email sending, unsubscribe, automatic notifications for task assignment/updates/comments, quote/invoice delivery, and staff notification when a client responds to a quote
- **Admin panel** — admin dashboard, user management (add/edit/delete), subscription plan management, system settings
- **CSRF protection** on all forms (Flask-WTF)
- **Security headers** via Flask-Talisman (`security.py`, wired into `api/index.py`) — CSP, HSTS, secure cookies, clickjacking/MIME-sniffing protection; HTTPS-forcing and secure-cookie behavior only activate when `FLASK_ENV=production` or `VERCEL=1`, so local `http://` dev still works

## Tech Stack

- **Flask** — web framework
- **Flask-SQLAlchemy** — ORM
- **PostgreSQL** (Neon) via `psycopg2-binary` — production database (SQLite fallback for local dev)
- **Flask-Login** — session/auth management
- **Flask-WTF / WTForms** — forms and CSRF protection
- **Flask-Mail** — transactional and notification email
- **PesaPal** — payment gateway for subscriptions and client invoices
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
│   ├── email_routes.py    # Bulk email + unsubscribe routes
│   ├── billing.py         # Staff-facing quote/invoice CRUD, send, convert quote→invoice
│   └── portal.py          # Public client portal: view/accept/decline quotes, view/pay invoices
├── models/
│   ├── models.py          # User, Client, Project, Task, Comment, TimeEntry, ProjectMember, ActivityLog
│   ├── subscription.py    # Subscription, SubscriptionPlan, Payment
│   └── billing.py         # Quote, QuoteItem, Invoice, InvoiceItem (each with a public_token for the portal)
├── services/               # Business logic services
├── utils/
│   ├── db_utils.py        # DB pool config, retry/safe query helpers
│   └── email_utils.py     # Email sending + notification templates
├── forms.py                # WTForms form definitions
├── filters.py               # Custom Jinja filters
├── security.py              # Talisman security headers, wired into api/index.py
├── config.py                 # App configuration
├── migrations/               # Manual DB migration scripts
├── templates/                # Jinja templates (auth, clients, projects, tasks, quotes, invoices, reports, subscription, admin, emails)
│   └── portal/                # Client-facing portal templates (separate branded layout, no internal nav/login)
├── static/                    # CSS and images (robots.txt and sitemap.xml are now served at the site root by api/index.py, built from BASE_URL)
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

- No automated test suite yet.
- No PDF export or e-signature for quotes/invoices — the client portal is web-only.
- No automatic invoice reminders for approaching/passed due dates (currently manual re-send).
