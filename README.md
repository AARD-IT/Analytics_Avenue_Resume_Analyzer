# Analytics Avenue · Resume Analyzer (AI chatbot for HR)

HR asks questions in plain English, and the bot replies with **candidate cards**. Each card has the candidate's name, email, phone, category, experience, industry sectors and matching projects, plus **📄 Resume** and **✉️ Email** buttons. Emails are sent from inside the app. Admins manage everything from the **Admin** tab.

```
HR:  data analysts with 3+ years in manufacturing
Bot: Found 4 candidates · Data Analytics · 3+ yrs · Manufacturing sector
     #1 Vignesh T  · 11.6 yrs · ✉️ … 📞 …  ▸ Operational Efficiency Report · Manufacturing   [📄 Resume] [✉️ Email] [👤 Details]
     …
HR:  among them only with power bi        → Narrowed to 2 candidates
HR:  does #2 know Tableau?                → answer from the profile (1 tiny AI call)
HR:  compare #1 and #2                    → side-by-side table + short verdict
HR:  send interview email to top 2 for Data Analyst Chennai
Bot: 2 drafts ready → review/edit → [📨 Send] [Cancel]     (nothing is sent without a click)
HR:  JD: <paste job description>          → ranked matches with % score
```

## Agent (LangGraph)

```
START → 🛡 guard_in ─┬─ blocked ─────────────────────────────────────► 🛡 guard_out → END
                     └► 🧭 route ─► search · refine · count · stats · profile · question ·
                                   compare · show_resume · match_jd · smalltalk ────► guard_out
                                 └► email_draft ─► ✋ approve (interrupt: Send/Cancel) ─► guard_out
```

| Node | What it does | Tokens |
|---|---|---|
| `guard_in` | Blocks searches on age, gender, religion, caste, marital status, nationality and disability. Blocks prompt-injection. Caps message length. | 0 |
| `route` | The rule parser handles most messages. A gpt-4.1-nano call happens only when the rules can't understand a message. Results are cached. | 0 / ~300 |
| `search` / `refine` / `count` / `stats` | Database filters. Refine narrows the previous result list. | 0 |
| `profile` / `show_resume` | Card with work history, projects, education and resume viewer. | 0 |
| `question` / `compare` | The LLM sees **only a ~200-token profile summary**, never the full resume. | ~600 |
| `match_jd` | Optional nano call to read the JD, then deterministic scoring (skills 55, category 20, experience 15, sector 10). | 0 / ~800 |
| `email_draft` → `approve` | Templates filled from the database. LangGraph `interrupt()` waits for HR to approve. Send applies the batch cap and cooldown. | 0 |
| `guard_out` | AI-written text can't leak contacts of candidates that aren't in context. Also caps length. | 0 |

The LangGraph checkpointer remembers each HR user's last result list, so *#2*, *top 3*, *them* and *among these* work across messages.

## Cost

| Action | Cost (OpenAI) |
|---|---|
| Read a new resume (once, cached by file hash) | ~$0.0025 (gpt-4.1-mini) |
| Most chat questions | **$0** (rules) |
| Unclear question / profile Q&A / compare | ~$0.0001 (gpt-4.1-nano) |
| Personalised emails (any number) | **$0** |
| Daily budget cap (Admin → Settings) | When reached, the bot switches to rules-only mode |

Admin → Usage shows cost per day, the number of AI calls and the percentage of questions answered without AI.

## Guardrails

- **Fair hiring:** filters on protected personal attributes are refused. Attribute requirements in a JD are ignored.
- **Prompt-injection:** blocked in messages. Resume text is treated as data only.
- **Grounded answers:** candidate lists always come from the database. AI answers are grounded in profiles, and contacts in AI text are filtered.
- **Email safety:**
  - Human approval is required before anything sends.
  - Batch limit and "don't re-email within N days" setting.
  - Every send is logged.
- **Access control:**
  - Google sign-in, or an access code.
  - Only approved emails can use the app, and only admins see the Admin tab.
  - A daily AI budget cap.

## Admin tab (admins only)

- **Resumes:**
  - Upload PDF/DOCX files, or sync Google Drive folders (paste links).
  - Run a one-click AI upgrade of old records.
  - Edit or delete candidates.
  - See files with issues.
- **Job openings:** title, category, location, mode, link, order and active flag. These are used in emails.
- **Templates:** edit, set the default, live preview, ✨ AI polish (1 call), placeholders.
- **Users:** add or remove HR users and admins.
- **Settings:**
  - Company and sender name, reply-to.
  - Models and daily AI budget.
  - Email batch limit, cooldown and delay.
  - System status.
- **Usage & logs:** AI cost, questions, email log, agent graph.

## Deploy (Streamlit Cloud)

1. **MongoDB Atlas**, free M0 cluster:
   - Create a database user.
   - In Network Access, allow `0.0.0.0/0`.
   - Copy the `mongodb+srv://…` URI.
2. **Google sign-in** (recommended):
   - In Google Cloud Console, go to Credentials and create an OAuth client ID (Web).
   - Set the redirect URI to `https://YOUR-APP.streamlit.app/oauth2callback`.
3. **Gmail:**
   - Turn on 2-Step Verification for the sending account.
   - Create an App password.
4. **Drive (optional):** create a service account and share the resume folder with its `client_email` (Viewer).
5. Push this folder to a **private** GitHub repo. On share.streamlit.io, set the main file to `app.py`.
6. Paste `.streamlit/secrets.toml.example`, with your values filled in, into **Settings → Secrets**. Put your email in `ADMIN_EMAILS`.
7. Open the app and sign in, then go to **Admin → Resumes → Upgrade now**. This re-reads the 99 bundled resumes with AI, a one-time cost of about $0.26.

The first start loads 99 already-processed candidates from `data/seed/` and `data/resumes/`, so the chat works right away.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

Without `MONGODB_URI` the app uses an in-memory database. Without `[auth]` or `ACCESS_CODE` any email can sign in, and the first person to sign in becomes admin. Both are fine for local development only.

## Project layout

```
app.py                  entry: login → pages (Chat, Admin)
agent/graph.py          LangGraph agent (nodes, edges, interrupt, checkpointer)
agent/router.py         rule parser + nano LLM router
agent/guardrails.py     input/output guardrails
agent/tools.py          search, stats, compact profiles, JD scoring
services/extract.py     1-call resume extraction → normalised record
services/ingest.py      PDF/DOCX text, Drive sync, batch pipeline (parallel)
services/mailer.py      templates, personalisation, SMTP, logging
core/store.py           MongoDB data layer (+ seed, users, settings, usage)
core/llm.py             OpenAI/Groq JSON-mode wrapper, fallback, budget, cost logging
core/auth.py            Google sign-in / access code, roles
core/taxonomy.py        categories, sectors, keyword classifiers
core/normalize.py       dates, experience merge, phone/email/name cleanup
ui/                     theme (blue/white), chat page, admin page, components
```
