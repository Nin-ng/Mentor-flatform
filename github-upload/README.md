# AI-Native Mentor Advisor Platform / Nền tảng Cố vấn AI

A single-file Streamlit app that turns a business plan into an interactive branch tree,
collects free-form mentor feedback, auto-classifies it into **Judge / Mentor / Co-worker**
columns via the Groq API, and auto-expands the tree ("Auto-Pilot") when a mentor proposes
a missing concept. Data is stored in **Postgres** so it survives redeploys and restarts.

## 1. Get a free Postgres database (one-time, ~2 minutes)

1. Go to **neon.tech** and sign up (free tier).
2. Create a new project — it gives you a connection string immediately, e.g.
   `postgresql://user:password@ep-xxxx.neon.tech/neondb?sslmode=require`.
3. Copy that string — you'll paste it as `DATABASE_URL` below and again in Streamlit
   Cloud's Secrets panel in step 3. Tables are created automatically the first time the
   app runs.

(Any other Postgres host — Supabase, Railway, your own server — works the same way; just
use its connection string.)

## 2. Local setup / Cài đặt

```powershell
pip install -r requirements.txt
copy .env.example .env
# edit .env: paste your GROQ_API_KEY and the DATABASE_URL from step 1
streamlit run app.py
```

- **Python 3.10+** is required (the code uses `X | Y` type hints).
- `GROQ_API_KEY` and `DATABASE_URL` must be set either in `.env` (local) or
  `.streamlit/secrets.toml` (local, or pasted into Streamlit Cloud's Secrets panel when
  deployed). There is no built-in fallback key — the app shows a clear error if either is
  missing.

## 3. Sharing a link with your user (Streamlit Community Cloud)

1. **Create a GitHub repo** at github.com/new (Public is fine, or Private if your GitHub
   plan allows private repos on Streamlit Cloud). Do not seed it with real customer data.
2. **Upload the files** — easiest without installing git: on the new repo's page click
   "uploading an existing file" and drag in `app.py`, `requirements.txt`, `README.md`,
   and `.gitignore`. **Do not upload** `.env` or `.streamlit/secrets.toml` — those stay
   local only, your database credentials would otherwise be public.
3. Go to **share.streamlit.io**, sign in with GitHub, click **"New app"**, pick your repo,
   branch `main`, and set the main file path to `app.py`.
4. Before clicking Deploy, open **"Advanced settings" → Secrets** and paste:
   ```toml
   GROQ_API_KEY = "your_real_groq_api_key"
   DATABASE_URL = "postgresql://user:password@ep-xxxx.neon.tech/neondb?sslmode=require"
   ```
5. Click **Deploy**. Streamlit Cloud gives you a permanent link like
   `https://your-app-name.streamlit.app` — that's what you send to your user. Every visit
   reads/writes the same Neon database, so data now persists across redeploys, restarts,
   and app sleeps.

**Still worth knowing:**
- The free tier is a shared, publicly-reachable app unless you set viewer restrictions in
  Streamlit Cloud's app settings.
- Neon's free tier has its own limits (storage size, auto-suspend after inactivity — it
  wakes back up automatically on the next query, just with a brief first-query delay).

## 4. Using the app / Cách sử dụng

1. Open the **🌳 Business Plan** tab.
2. Expand **📂 Initialize Business Framework**:
   - Paste a Markdown business plan and click **🏗️ Build Plan via Groq**, or
   - Click **⚡ Mock Bank A CRM AI Agent Plan** to load a ready-made demo tree.
3. Pick a branch from the sidebar tree or the **🔀 Quick branch switch** dropdown.
4. Type feedback in **💬 Gửi ý kiến của bạn** and click **Submit Advice**.
   - Groq classifies it as a Judge critique, Mentor resource, or Co-worker signup and
     files it into the matching Kanban column.
   - If Groq detects a missing concept, it automatically creates a new sub-branch under
     the current node ("Auto-Pilot") and shows a toast notification.
5. Check the **🛠️ Admin Audit Log** tab to see every Groq API call (payload, response,
   duration) for compliance review.
6. See **🛠️ D-Day Demo Verification Scenarios** at the bottom of the Business Plan tab
   for a scripted walkthrough.

## 5. Compliance notes / Ghi chú tuân thủ

- Vietnamese banking abbreviations (KH, RM, NH, SME, ĐNCV/DNCV) are expanded before being
  sent to Groq, so the model understands both accented and unaccented shorthand.
- Emails, phone numbers, and ID-like digit sequences are redacted from all outbound text
  before it is sent to Groq, in line with Decree 13/2023/NĐ-CP on personal data
  protection. No raw PII is stored in the database either.

## 6. Project files

| File                              | Purpose                                        |
|------------------------------------|-------------------------------------------------|
| `app.py`                           | The entire application (UI + DB + Groq layer)   |
| `requirements.txt`                 | Python dependencies                             |
| `.env.example`                     | Template for your local `.env`                  |
| `.streamlit/secrets.toml.example`  | Template for local Streamlit secrets            |
| `.gitignore`                       | Keeps `.env` / secrets / caches out of git       |
