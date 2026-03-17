import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from google import genai

import database

load_dotenv()

SCHEMA_PATH = Path(__file__).parent / "schema.json"
schema = json.loads(SCHEMA_PATH.read_text())
FIELDS = schema["fields"]
LIST_FIELDS = {f["key"] for f in FIELDS if f["type"] == "list"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_gemini_client():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        st.error("GEMINI_API_KEY not set in environment.")
        st.stop()
    return genai.Client(api_key=api_key)


def clean_json(text: str) -> str:
    import re
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"\s*```", "", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text.strip()


def load_all_interviews() -> pd.DataFrame:
    with database.get_connection() as conn:
        return pd.read_sql("SELECT * FROM interviews ORDER BY created_at DESC", conn)


def load_interview(interview_id: int) -> dict | None:
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM interviews WHERE id = ?", (interview_id,)
        ).fetchone()
    return dict(row) if row else None


def load_syntheses() -> list[dict]:
    with database.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, created_at FROM syntheses ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def load_synthesis_content(synthesis_id: int) -> str:
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT content FROM syntheses WHERE id = ?", (synthesis_id,)
        ).fetchone()
    return row["content"] if row else ""


def expand_list_field(df: pd.DataFrame, col: str) -> list[str]:
    items: list[str] = []
    for val in df[col].dropna():
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                items.extend(str(x) for x in parsed)
        except (json.JSONDecodeError, TypeError):
            items.append(str(val))
    return items


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def view_macro_dashboard(df: pd.DataFrame) -> None:
    st.header("Macro Dashboard")

    if df.empty:
        st.info("No interviews processed yet. Run `python watcher.py` to start.")
        return

    # Summary metrics
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Interviews", len(df))
    bank_pct = int(df["has_bank_account"].fillna(0).astype(bool).mean() * 100)
    pan_pct = int(df["has_pan_card"].fillna(0).astype(bool).mean() * 100)
    col2.metric("Have Bank Account", f"{bank_pct}%")
    col3.metric("Have PAN Card", f"{pan_pct}%")
    date_range = f"{df['created_at'].min()[:10]} – {df['created_at'].max()[:10]}"
    col4.metric("Date Range", date_range)

    st.divider()

    row1_l, row1_r = st.columns(2)

    with row1_l:
        if "gender" in df.columns:
            gc = df["gender"].value_counts().reset_index()
            gc.columns = ["gender", "count"]
            fig = px.pie(gc, names="gender", values="count", title="Gender Split",
                         color_discrete_sequence=px.colors.qualitative.Set2)
            st.plotly_chart(fig, use_container_width=True)

    with row1_r:
        if "monthly_income_range" in df.columns:
            ic = df["monthly_income_range"].value_counts().reset_index()
            ic.columns = ["range", "count"]
            fig = px.bar(ic, x="range", y="count", title="Monthly Income Distribution",
                         color_discrete_sequence=["#4C8BF5"])
            st.plotly_chart(fig, use_container_width=True)

    row2_l, row2_r = st.columns(2)

    with row2_l:
        items = expand_list_field(df, "loan_apps_used")
        if items:
            from collections import Counter
            counts = Counter(items).most_common(10)
            ldf = pd.DataFrame(counts, columns=["app", "count"])
            fig = px.bar(ldf, y="app", x="count", orientation="h",
                         title="Top Loan Apps Used",
                         color_discrete_sequence=["#36A2EB"])
            st.plotly_chart(fig, use_container_width=True)

    with row2_r:
        if "interest_vs_fee_pref" in df.columns:
            pc = df["interest_vs_fee_pref"].value_counts().reset_index()
            pc.columns = ["pref", "count"]
            fig = px.pie(pc, names="pref", values="count",
                         title="Interest vs Upfront Fee Preference",
                         color_discrete_sequence=px.colors.qualitative.Pastel)
            st.plotly_chart(fig, use_container_width=True)

    row3_l, row3_r = st.columns(2)

    with row3_l:
        if "preferred_tenure" in df.columns:
            tc = df["preferred_tenure"].value_counts().reset_index()
            tc.columns = ["tenure", "count"]
            fig = px.bar(tc, x="tenure", y="count", title="Preferred Loan Tenure",
                         color_discrete_sequence=["#FF6384"])
            st.plotly_chart(fig, use_container_width=True)

    with row3_r:
        if "preferred_amount_range" in df.columns:
            ac = df["preferred_amount_range"].value_counts().reset_index()
            ac.columns = ["range", "count"]
            fig = px.bar(ac, x="range", y="count", title="Preferred Loan Amount Range",
                         color_discrete_sequence=["#FFCE56"])
            st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("AI Synthesis")

    if "synthesizing" not in st.session_state:
        st.session_state["synthesizing"] = False

    if st.button("Run Synthesis", disabled=st.session_state["synthesizing"]):
        st.session_state["synthesizing"] = True
        client = get_gemini_client()
        rows_text = df.to_string(index=False)
        prompt = (
            "You are a senior UX researcher specialising in financial inclusion.\n"
            "Given the following interview data, produce:\n"
            "1. 3–5 distinct user personas with names, traits, financial profile, and a representative quote\n"
            "2. Cohort groupings by financial profile\n"
            "3. Top 3 actionable product design insights\n"
            "Return structured Markdown.\n\n"
            f"--- INTERVIEW DATA ---\n{rows_text}"
        )
        with st.spinner("Generating personas and synthesis…"):
            try:
                response = client.models.generate_content(
                    model="gemini-1.5-pro", contents=prompt
                )
                content = response.text
                now = datetime.now(timezone.utc).isoformat()
                with database.get_connection() as conn:
                    conn.execute(
                        "INSERT INTO syntheses (content, created_at) VALUES (?, ?)",
                        (content, now),
                    )
                    conn.commit()
                st.success("Synthesis complete!")
                st.markdown(content)
            except Exception as exc:
                st.error(f"Synthesis failed: {exc}")
            finally:
                st.session_state["synthesizing"] = False

    syntheses = load_syntheses()
    if syntheses:
        st.subheader("Synthesis History")
        options = {f"#{s['id']} — {s['created_at'][:19]}": s["id"] for s in syntheses}
        selected_label = st.selectbox("Select a past synthesis", list(options.keys()))
        if selected_label:
            past = load_synthesis_content(options[selected_label])
            st.markdown(past)


def view_directory(df: pd.DataFrame) -> None:
    st.header("Interview Directory")

    if df.empty:
        st.info("No interviews found.")
        return

    display_cols = ["id", "source_file", "respondent_name", "age", "gender",
                    "location", "needs_reprocessing", "created_at"]
    display_cols = [c for c in display_cols if c in df.columns]
    display_df = df[display_cols].copy()

    def highlight_reprocessing(row):
        if row.get("needs_reprocessing", 0):
            return ["background-color: #fff3cd"] * len(row)
        return [""] * len(row)

    st.dataframe(
        display_df.style.apply(highlight_reprocessing, axis=1),
        use_container_width=True,
    )

    st.caption("Select an interview ID below to open the Detail view.")
    ids = df["id"].tolist()
    selected_id = st.selectbox("Interview ID", ids, key="dir_select")
    if st.button("Open Detail & Edit"):
        st.session_state["selected_id"] = selected_id
        st.session_state["view"] = "Detail & Edit"
        st.rerun()


def view_detail(interview_id: int) -> None:
    row = load_interview(interview_id)
    if not row:
        st.error(f"Interview #{interview_id} not found.")
        return

    st.header(f"Interview #{interview_id} — {row.get('source_file', '')}")

    col_left, col_right = st.columns([1, 1])

    with col_left:
        st.subheader("Extracted Insights")
        for field in FIELDS:
            key = field["key"]
            val = row.get(key)
            if val is None:
                continue
            label = field["label"]
            if field["type"] == "list":
                try:
                    items = json.loads(val)
                    st.markdown(f"**{label}**")
                    for item in items:
                        st.markdown(f"- {item}")
                except (json.JSONDecodeError, TypeError):
                    st.markdown(f"**{label}:** {val}")
            else:
                st.markdown(f"**{label}:** {val}")

    with col_right:
        st.subheader("Transcript")
        new_transcript = st.text_area(
            "Edit transcript",
            value=row.get("full_transcript") or "",
            height=400,
            key=f"transcript_{interview_id}",
        )

        if st.button("Save Transcript", key=f"save_{interview_id}"):
            now = datetime.now(timezone.utc).isoformat()
            with database.get_connection() as conn:
                conn.execute(
                    "UPDATE interviews SET full_transcript = ?, needs_reprocessing = 1, updated_at = ? WHERE id = ?",
                    (new_transcript, now, interview_id),
                )
                conn.commit()
            st.toast("Transcript saved. Re-extraction available.")
            st.rerun()

    if row.get("needs_reprocessing"):
        st.warning("This interview has an edited transcript. Re-extract insights to sync.")

        reextract_key = f"reextracting_{interview_id}"
        if reextract_key not in st.session_state:
            st.session_state[reextract_key] = False

        if st.button("Re-Extract Insights", key=f"reextract_{interview_id}",
                     disabled=st.session_state[reextract_key]):
            st.session_state[reextract_key] = True
            client = get_gemini_client()

            field_list = "\n".join(f"  - {f['key']}: {f['label']}" for f in FIELDS)
            keys = [f["key"] for f in FIELDS]
            list_keys = [f["key"] for f in FIELDS if f["type"] == "list"]
            bool_keys = [f["key"] for f in FIELDS if f["type"] == "boolean"]
            prompt = (
                "You are a UX research analyst. Given the transcript below, extract the following fields "
                "and return a single JSON object with EXACTLY these keys:\n"
                f"{json.dumps(keys)}\n\n"
                f"{field_list}\n\n"
                f"List fields ({', '.join(list_keys)}): return JSON arrays.\n"
                f"Boolean fields ({', '.join(bool_keys)}): return true/false.\n"
                "If unknown, return null. Do NOT wrap in markdown fences.\n\n"
                f"--- TRANSCRIPT ---\n{new_transcript}"
            )
            with st.spinner("Re-extracting insights…"):
                try:
                    response = client.models.generate_content(
                        model="gemini-1.5-pro", contents=prompt
                    )
                    data = json.loads(clean_json(response.text))

                    set_clauses = []
                    values = []
                    for field in FIELDS:
                        key = field["key"]
                        val = data.get(key)
                        if field["type"] == "list" and isinstance(val, list):
                            val = json.dumps(val)
                        set_clauses.append(f"{key} = ?")
                        values.append(val)

                    now = datetime.now(timezone.utc).isoformat()
                    set_clauses += ["needs_reprocessing = ?", "updated_at = ?"]
                    values += [0, now, interview_id]

                    with database.get_connection() as conn:
                        conn.execute(
                            f"UPDATE interviews SET {', '.join(set_clauses)} WHERE id = ?",
                            values,
                        )
                        conn.commit()
                    st.success("Insights updated.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Re-extraction failed: {exc}")
                finally:
                    st.session_state[reextract_key] = False


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _render_login() -> None:
    _, col, _ = st.columns([1, 2, 1])
    with col:
        st.title("PDUx Research Synthesizer")
        st.subheader("Sign In")
        with st.form("login_form"):
            username = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign In", use_container_width=True)
        if submitted:
            valid_user = st.secrets.get("auth", {}).get("username", "")
            valid_pass = st.secrets.get("auth", {}).get("password", "")
            if username == valid_user and password == valid_pass:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Invalid credentials.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(page_title="PDUx Research Synthesizer", layout="wide")
    database.init_db()

    if not st.session_state.get("authenticated"):
        _render_login()
        st.stop()

    if "view" not in st.session_state:
        st.session_state["view"] = "Macro Dashboard"
    if "selected_id" not in st.session_state:
        st.session_state["selected_id"] = None

    with st.sidebar:
        st.title("PDUx Research")
        st.session_state["view"] = st.radio(
            "Navigate",
            ["Macro Dashboard", "Directory", "Detail & Edit"],
            index=["Macro Dashboard", "Directory", "Detail & Edit"].index(
                st.session_state["view"]
            ),
        )

    df = load_all_interviews()
    view = st.session_state["view"]

    if view == "Macro Dashboard":
        view_macro_dashboard(df)
    elif view == "Directory":
        view_directory(df)
    elif view == "Detail & Edit":
        if st.session_state["selected_id"] is None:
            st.info("Select an interview from the Directory first.")
        else:
            view_detail(st.session_state["selected_id"])


if __name__ == "__main__":
    main()
