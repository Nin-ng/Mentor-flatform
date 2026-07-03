"""AI-Native Mentor Advisor Platform — Streamlit + Postgres + Groq REST."""

import html
import json
import os
import re
import time
from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import requests
import streamlit as st
from dotenv import load_dotenv

# ----------------------------------------------------------------------------
# STAGE 1: ENVIRONMENT & CONFIG
# ----------------------------------------------------------------------------

load_dotenv()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL_LARGE = "llama3-70b-8192"
GROQ_MODEL_SMALL = "llama3-8b-8192"

ROLE_COLORS = {"Judge": "#ff4b4b", "Mentor": "#1c83e1", "Co-worker": "#21c354"}
ROLE_ICONS = {"Judge": "❌", "Mentor": "📚", "Co-worker": "🤝"}
ROLE_BADGES = {"Judge": "🔴", "Mentor": "🔵", "Co-worker": "🟢"}


def get_api_key() -> str | None:
    """Prefer Streamlit Cloud secrets, fall back to a local .env var. No hardcoded key."""
    try:
        secret_key = st.secrets.get("GROQ_API_KEY")
    except Exception:  # noqa: BLE001 - no secrets.toml present locally, that's fine
        secret_key = None
    return secret_key or os.getenv("GROQ_API_KEY")


def get_database_url() -> str | None:
    """Prefer Streamlit Cloud secrets, fall back to a local .env var."""
    try:
        secret_url = st.secrets.get("DATABASE_URL")
    except Exception:  # noqa: BLE001 - no secrets.toml present locally, that's fine
        secret_url = None
    return secret_url or os.getenv("DATABASE_URL")


# ----------------------------------------------------------------------------
# STAGE 1.3: DATABASE INITIALIZATION (Postgres — persists across redeploys)
# ----------------------------------------------------------------------------

@contextmanager
def db_cursor():
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError(
            "No DATABASE_URL configured. Set it in .streamlit/secrets.toml (cloud) or .env "
            "(local) — see README for how to get a free Postgres connection string."
        )
    conn = psycopg2.connect(database_url, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn:
            with conn.cursor() as cur:
                yield cur
    finally:
        conn.close()


def init_db() -> None:
    with db_cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS branches (
                id SERIAL PRIMARY KEY,
                parent_id INTEGER REFERENCES branches(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                description TEXT
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                branch_id INTEGER REFERENCES branches(id) ON DELETE CASCADE,
                author TEXT NOT NULL DEFAULT 'Anonymous Mentor',
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                evidence_or_links TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id SERIAL PRIMARY KEY,
                endpoint_called TEXT NOT NULL,
                payload_sent TEXT,
                response_received TEXT,
                execution_time_sec REAL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )


# ----------------------------------------------------------------------------
# DATA ACCESS HELPERS
# ----------------------------------------------------------------------------

def get_all_branches() -> list[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM branches ORDER BY id")
        return cur.fetchall()


def get_branch(branch_id: int) -> dict | None:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM branches WHERE id = %s", (branch_id,))
        return cur.fetchone()


def insert_branch(parent_id: int | None, title: str, description: str) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO branches (parent_id, title, description) VALUES (%s, %s, %s) RETURNING id",
            (parent_id, title, description),
        )
        return cur.fetchone()["id"]


def insert_feedback(branch_id: int, author: str, role: str, content: str, evidence: str) -> int:
    with db_cursor() as cur:
        cur.execute(
            "INSERT INTO feedback (branch_id, author, role, content, evidence_or_links) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (branch_id, author or "Anonymous Mentor", role, content, evidence),
        )
        return cur.fetchone()["id"]


def get_feedback_for_branch(branch_id: int) -> list[dict]:
    with db_cursor() as cur:
        cur.execute(
            "SELECT * FROM feedback WHERE branch_id = %s ORDER BY timestamp DESC", (branch_id,)
        )
        return cur.fetchall()


def get_branch_badges() -> dict[int, set[str]]:
    with db_cursor() as cur:
        cur.execute("SELECT DISTINCT branch_id, role FROM feedback")
        rows = cur.fetchall()
    badges: dict[int, set[str]] = {}
    for row in rows:
        badges.setdefault(row["branch_id"], set()).add(row["role"])
    return badges


def get_audit_logs() -> list[dict]:
    with db_cursor() as cur:
        cur.execute("SELECT * FROM audit_logs ORDER BY timestamp DESC")
        return cur.fetchall()


def log_audit(endpoint: str, payload: str, response: str, exec_time: float) -> None:
    # Called from call_groq()'s `finally` block — must never raise, or it would mask the
    # original Groq error and crash the calling flow on top of it.
    try:
        with db_cursor() as cur:
            cur.execute(
                "INSERT INTO audit_logs (endpoint_called, payload_sent, response_received, "
                "execution_time_sec) VALUES (%s, %s, %s, %s)",
                (endpoint, payload, response, exec_time),
            )
    except Exception as exc:  # noqa: BLE001
        print(f"[audit_logs] failed to record log: {exc}")


def seed_mock_bank_a() -> None:
    root_id = insert_branch(
        None,
        "CRM Smart Agent",
        "Bank A's AI-native CRM agent that assists Relationship Managers with customer "
        "engagement, personalization, and regulatory compliance.",
    )
    insert_branch(
        root_id,
        "MCP Server Integration",
        "Model Context Protocol server that connects the CRM agent to internal banking "
        "tools, customer data sources, and third-party APIs.",
    )
    insert_branch(
        root_id,
        "RM Personalization Engine",
        "Recommendation engine that tailors product offers and outreach scripts to each "
        "Relationship Manager's customer portfolio.",
    )
    insert_branch(
        root_id,
        "Regulatory Audit Logs",
        "Compliance-grade logging subsystem that records every AI-assisted decision for "
        "later regulatory review.",
    )


# ----------------------------------------------------------------------------
# STAGE 3.1: VIETNAMESE BANKING ABBREVIATIONS
# ----------------------------------------------------------------------------

VN_ABBREVIATIONS = {
    "KH": "Khách hàng (Customer)",
    "RM": "Relationship Manager (Quan hệ khách hàng)",
    "NH": "Ngân hàng (Bank)",
    "SME": "Doanh nghiệp vừa và nhỏ (Small & Medium Enterprise)",
    "ĐNCV": "Điều chỉnh nghiệp vụ (Business Adjustment)",
    "DNCV": "Điều chỉnh nghiệp vụ (Business Adjustment)",
}

_VN_ABBR_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in VN_ABBREVIATIONS) + r")\b",
    re.IGNORECASE | re.UNICODE,
)


def normalize_vn_abbreviations(text: str) -> str:
    def _expand(match: re.Match) -> str:
        key = match.group(1).upper()
        expansion = VN_ABBREVIATIONS.get(key)
        return f"{match.group(1)} ({expansion})" if expansion else match.group(1)

    return _VN_ABBR_PATTERN.sub(_expand, text)


# ----------------------------------------------------------------------------
# STAGE 3.2: PII ANONYMIZATION (Decree 13/2023/NĐ-CP compliance)
# ----------------------------------------------------------------------------

_PHONE_PATTERN = re.compile(r"(\+?84|0)(\s|\.)?(\d(\s|\.)?){8,10}")
_ID_PATTERN = re.compile(r"\b\d{9,12}\b")
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def anonymize_pii(text: str) -> str:
    text = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", text)
    text = _PHONE_PATTERN.sub("[PHONE_REDACTED]", text)
    text = _ID_PATTERN.sub("[ID_REDACTED]", text)
    return text


# ----------------------------------------------------------------------------
# STAGE 2: GROQ API SERVICE LAYER
# ----------------------------------------------------------------------------

def extract_json(raw_text: str):
    """Pull the first valid JSON object/array out of an LLM response."""
    if not raw_text:
        return None
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
    return None


def call_groq(
    system_prompt: str,
    user_prompt: str,
    endpoint_label: str,
    model: str = GROQ_MODEL_LARGE,
    temperature: float = 0.2,
    max_tokens: int = 2000,
) -> str:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    api_key = get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    start = time.time()
    response_text = ""
    try:
        if not api_key:
            raise RuntimeError(
                "No GROQ_API_KEY configured. Set it in .streamlit/secrets.toml (cloud) "
                "or .env (local)."
            )
        resp = requests.post(GROQ_API_URL, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        response_text = data["choices"][0]["message"]["content"]
    except Exception as exc:  # noqa: BLE001 - surface any failure into the audit log
        response_text = f"ERROR: {exc}"
    finally:
        elapsed = time.time() - start
        log_audit(endpoint_label, json.dumps(payload, ensure_ascii=False), response_text, elapsed)
    return response_text


BUSINESS_PLAN_SYSTEM_PROMPT = """You are a master startup architect. Analyze the provided \
Markdown business plan and extract a nested hierarchy of key structural sections (e.g., Value \
Proposition, Market Fit, Technical Architecture, Financial Projections). You must output a \
strict, validated JSON array of objects with no conversational text and no markdown code fences. \
Output format:
[
  {
    "title": "Name of Section",
    "description": "Short executive summary",
    "sub_branches": [
      {"title": "Sub-Section Name", "description": "Summary"}
    ]
  }
]"""

FEEDBACK_CLASSIFIER_SYSTEM_PROMPT = """You are an expert startup advisor and linguist. Analyze \
the mentor's feedback regarding a specific business plan branch and perform these three \
concurrent tasks:

1. CLASSIFY ROLE: Classify the mentor's intent into exactly one of three roles:
   - "Judge": If they are pointing out errors, risks, compliance issues, or flaws.
   - "Mentor": If they are suggesting improvements, adding guides, or linking sources.
   - "Co-worker": If they express interest in joining, co-founding, or coding the feature.

2. EXTRACT DETAILS:
   - For Judges: Extract what is wrong and what is the evidence/reasoning.
   - For Mentors: Extract the recommended resources, reference links, or books.
   - For Co-workers: Extract the skills offered.

3. DYNAMIC BRANCH DETECTION: Detect if the mentor is suggesting a major missing concept, \
feature, strategy, or module that does not exist in the current business plan branch. If yes, \
set "new_branch_detected" to true, and provide a "suggested_branch" containing a "title" (max 5 \
words) and a "description". If no, set "new_branch_detected" to false and "suggested_branch" to \
null.

Return strict JSON only, no conversational text, no markdown code fences, in exactly this \
layout:
{
  "role": "Judge" | "Mentor" | "Co-worker",
  "clean_feedback": "A polished, clear summary of the comment",
  "extracted_details": "Evidence, links, or skills description",
  "new_branch_detected": true | false,
  "suggested_branch": {"title": "Sub-branch Title", "description": "Brief explanation"} | null
}"""


def parse_business_plan_via_groq(markdown_text: str) -> list | None:
    safe_text = anonymize_pii(markdown_text)
    raw = call_groq(BUSINESS_PLAN_SYSTEM_PROMPT, safe_text, "business_plan_parser")
    parsed = extract_json(raw)
    return parsed if isinstance(parsed, list) else None


def classify_feedback_via_groq(branch_title: str, branch_description: str, feedback_text: str) -> dict:
    normalized = normalize_vn_abbreviations(feedback_text)
    safe_text = anonymize_pii(normalized)
    user_prompt = (
        f"Business plan branch title: {branch_title}\n"
        f"Branch description: {branch_description}\n"
        f"Mentor feedback: {safe_text}"
    )
    raw = call_groq(FEEDBACK_CLASSIFIER_SYSTEM_PROMPT, user_prompt, "feedback_classifier")
    parsed = extract_json(raw)
    if not isinstance(parsed, dict) or parsed.get("role") not in ROLE_COLORS:
        return {
            "role": "Mentor",
            "clean_feedback": feedback_text,
            "extracted_details": "",
            "new_branch_detected": False,
            "suggested_branch": None,
        }
    parsed.setdefault("clean_feedback", feedback_text)
    parsed.setdefault("extracted_details", "")
    parsed.setdefault("new_branch_detected", False)
    parsed.setdefault("suggested_branch", None)
    return parsed


# ----------------------------------------------------------------------------
# TREE HELPERS
# ----------------------------------------------------------------------------

def build_tree(branches: list[dict]) -> dict:
    nodes = {row["id"]: {"row": row, "children": []} for row in branches}
    roots = []
    for row in branches:
        node = nodes[row["id"]]
        if row["parent_id"] and row["parent_id"] in nodes:
            nodes[row["parent_id"]]["children"].append(node)
        else:
            roots.append(node)
    return {"roots": roots, "nodes": nodes}


def flatten_tree(roots: list[dict], depth: int = 0) -> list[tuple[dict, int]]:
    flat = []
    for node in roots:
        flat.append((node, depth))
        flat.extend(flatten_tree(node["children"], depth + 1))
    return flat


def branch_label(node: dict, depth: int, badges: dict[int, set[str]]) -> str:
    row = node["row"]
    prefix = ("— " * depth) if depth else ""
    badge_str = "".join(ROLE_BADGES[r] for r in ("Judge", "Mentor", "Co-worker") if r in badges.get(row["id"], set()))
    badge_str = f"{badge_str} " if badge_str else ""
    return f"{prefix}{badge_str}{row['title']}"


# ----------------------------------------------------------------------------
# UI RENDERING HELPERS
# ----------------------------------------------------------------------------

def render_card(role: str, author: str, content: str, extracted: str, timestamp: str) -> None:
    color = ROLE_COLORS[role]
    icon = ROLE_ICONS[role]
    safe_author = html.escape(author or "Anonymous Mentor")
    safe_content = html.escape(content or "")
    safe_extracted = html.escape(extracted or "—")
    safe_timestamp = html.escape(str(timestamp or ""))
    st.markdown(
        f"""
        <div style="border-left:4px solid {color}; background:{color}1a; padding:10px 14px;
                    border-radius:8px; margin-bottom:10px;">
            <div style="font-weight:600; font-size:0.95em;">{icon} {safe_author}</div>
            <div style="margin:6px 0; font-size:0.9em;">{safe_content}</div>
            <div style="font-size:0.8em; opacity:0.75;">📎 {safe_extracted}</div>
            <div style="font-size:0.72em; opacity:0.5; margin-top:4px;">{safe_timestamp}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ----------------------------------------------------------------------------
# STREAMLIT APP
# ----------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="AI-Native Mentor Advisor Platform", page_icon="🧭", layout="wide")
    init_db()

    if "selected_branch_id" not in st.session_state:
        st.session_state.selected_branch_id = None

    st.title("🧭 AI-Native Mentor Advisor Platform")

    tab_plan, tab_audit = st.tabs(["🌳 Business Plan", "🛠️ Admin Audit Log"])

    with tab_plan:
        render_setup_wizard()

        branches = get_all_branches()
        if not branches:
            st.info("No business plan loaded yet. Use '📂 Initialize Business Framework' above to get started.")
        else:
            tree = build_tree(branches)
            flat = flatten_tree(tree["roots"])
            badges = get_branch_badges()

            render_sidebar_tree(flat, badges)
            render_quick_switch(flat, badges)

            selected = get_branch(st.session_state.selected_branch_id) if st.session_state.selected_branch_id else None
            if selected:
                render_branch_panel(selected)

        render_demo_footer()

    with tab_audit:
        render_audit_log()


def render_setup_wizard() -> None:
    with st.expander("📂 Initialize Business Framework", expanded=False):
        st.markdown("**Option A — Paste a Markdown business plan and let Groq structure it:**")
        markdown_text = st.text_area(
            "Paste your business plan Markdown here", height=200, key="markdown_input"
        )
        if st.button("🏗️ Build Plan via Groq", type="primary"):
            if not markdown_text.strip():
                st.warning("Please paste some Markdown content first.")
            else:
                with st.spinner("Groq is structuring your business plan..."):
                    sections = parse_business_plan_via_groq(markdown_text)
                if not sections:
                    st.error("Groq could not produce a valid structure. Please check the API key or try again.")
                else:
                    for section in sections:
                        root_id = insert_branch(
                            None, section.get("title", "Untitled Section"), section.get("description", "")
                        )
                        for sub in section.get("sub_branches", []) or []:
                            insert_branch(root_id, sub.get("title", "Untitled"), sub.get("description", ""))
                    st.success("Business plan structured and loaded.")
                    st.rerun()

        st.markdown("---")
        st.markdown("**Option B — Load a ready-made demo plan:**")
        if st.button("⚡ Mock Bank A CRM AI Agent Plan"):
            seed_mock_bank_a()
            st.success("Mock Bank A CRM plan seeded.")
            st.rerun()


def render_sidebar_tree(flat: list[tuple[dict, int]], badges: dict[int, set[str]]) -> None:
    with st.sidebar:
        st.header("📁 Branch Navigation")
        for node, depth in flat:
            row = node["row"]
            label = branch_label(node, depth, badges)
            if st.button(label, key=f"nav_{row['id']}", use_container_width=True):
                st.session_state.selected_branch_id = row["id"]
                st.rerun()


def render_quick_switch(flat: list[tuple[dict, int]], badges: dict[int, set[str]]) -> None:
    options = [node["row"]["id"] for node, _ in flat]
    labels = {node["row"]["id"]: branch_label(node, depth, badges) for node, depth in flat}
    if st.session_state.selected_branch_id not in options:
        st.session_state.selected_branch_id = options[0]
    chosen = st.selectbox(
        "🔀 Quick branch switch",
        options=options,
        format_func=lambda bid: labels.get(bid, str(bid)),
        index=options.index(st.session_state.selected_branch_id),
        key="quick_switch_select",
    )
    if chosen != st.session_state.selected_branch_id:
        st.session_state.selected_branch_id = chosen
        st.rerun()


def render_branch_panel(selected: dict) -> None:
    st.subheader(selected["title"])
    st.write(selected["description"] or "_No description provided._")

    with st.form(key=f"feedback_form_{selected['id']}", clear_on_submit=True):
        author = st.text_input("Your name (optional)", value="")
        feedback_text = st.text_area(
            "💬 Gửi ý kiến của bạn (Ask as Judge, Mentor, or Co-worker)", height=100
        )
        submitted = st.form_submit_button("Submit Advice", type="primary")

    if submitted:
        if not feedback_text.strip():
            st.warning("Please write some feedback before submitting.")
        else:
            with st.spinner("Groq is analyzing the feedback..."):
                result = classify_feedback_via_groq(
                    selected["title"], selected["description"] or "", feedback_text
                )
            insert_feedback(
                selected["id"],
                author,
                result["role"],
                result["clean_feedback"],
                result["extracted_details"],
            )
            suggested = result.get("suggested_branch")
            if result.get("new_branch_detected") and suggested and suggested.get("title"):
                new_id = insert_branch(selected["id"], suggested["title"], suggested.get("description", ""))
                insert_feedback(
                    new_id, author, result["role"], result["clean_feedback"], result["extracted_details"]
                )
                st.session_state.selected_branch_id = new_id
                st.toast(
                    f"🚀 Auto-Pilot: New business sub-branch '{suggested['title']}' has been "
                    "automatically added under this node!"
                )
            st.rerun()

    feedback_rows = get_feedback_for_branch(selected["id"])
    judge_rows = [r for r in feedback_rows if r["role"] == "Judge"]
    mentor_rows = [r for r in feedback_rows if r["role"] == "Mentor"]
    coworker_rows = [r for r in feedback_rows if r["role"] == "Co-worker"]

    col_judge, col_mentor, col_coworker = st.columns(3)
    with col_judge:
        st.markdown("#### ❌ Judge Critiques")
        if not judge_rows:
            st.caption("No critiques yet.")
        for r in judge_rows:
            render_card("Judge", r["author"], r["content"], r["evidence_or_links"], r["timestamp"])
    with col_mentor:
        st.markdown("#### 📚 Mentor Resources")
        if not mentor_rows:
            st.caption("No resources yet.")
        for r in mentor_rows:
            render_card("Mentor", r["author"], r["content"], r["evidence_or_links"], r["timestamp"])
    with col_coworker:
        st.markdown("#### 🤝 Co-worker Signups")
        if not coworker_rows:
            st.caption("No signups yet.")
        for r in coworker_rows:
            render_card("Co-worker", r["author"], r["content"], r["evidence_or_links"], r["timestamp"])


def render_audit_log() -> None:
    st.subheader("🛠️ Admin Audit Log")
    logs = get_audit_logs()
    if not logs:
        st.info("No Groq API calls have been logged yet.")
        return
    for log in logs:
        with st.expander(f"[{log['timestamp']}] {log['endpoint_called']} — {log['execution_time_sec']:.2f}s"):
            st.markdown("**Payload sent:**")
            st.code(log["payload_sent"] or "", language="json")
            st.markdown("**Response received:**")
            st.code(log["response_received"] or "", language="json")


def render_demo_footer() -> None:
    with st.expander("🛠️ D-Day Demo Verification Scenarios", expanded=False):
        st.markdown(
            """
1. Click the **⚡ Mock Bank A CRM AI Agent Plan** button to seed the database with the core CRM problem outline.
2. Select the **MCP Server Integration** branch in the Navigation dropdown.
3. In the Feedback input, write: *"RM needs to call customer contacts but our API might break under high traffic, we must use an exponential backoff retry policy."*
4. Click **Submit Advice**.
5. Watch the app automatically identify the role as a **Judge** and instantly add a red warning card in the Critique column.
6. Now write: *"We are missing a core database schema module for Bank A CRM. We should build a Postgres client adapter."*
7. Watch the **Auto-Pilot** trigger: the tree dynamically expands, generating a new **Postgres Client Adapter** sub-node in real-time!
            """
        )


if __name__ == "__main__":
    main()
