from flask import Flask, render_template, request, redirect, session, flash, url_for, g
import mysql.connector
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from google import genai
from google.genai import types

# load_dotenv() - Moved to the bottom to avoid overriding Railway env vars

# ---------------- DB CONNECTION ----------------
def get_db():
    if 'db' not in g:
        try:
            # Version Check: 1.0.1 (New Diagnostics)
            print(">>> TELEMED DB CONNECT ATTEMPT v1.0.1 <<<")
            # Debug print to see what variables Railway is providing
            print(f"DEBUG ENV: MYSQLHOST={os.getenv('MYSQLHOST')}, MYSQL_HOST={os.getenv('MYSQL_HOST')}, DB_HOST={os.getenv('DB_HOST')}")
            
            # 1. Try Railway's auto-generated DATABASE_URL or MYSQL_URL
            database_url = os.getenv("DATABASE_URL") or os.getenv("MYSQL_URL")
            
            if database_url and database_url.startswith("mysql://"):
                from urllib.parse import urlparse
                parsed = urlparse(database_url)
                print(f"DEBUG: Connecting via URL (Host: {parsed.hostname}, Port: {parsed.port or 3306})")
                
                g.db = mysql.connector.connect(
                    host=parsed.hostname,
                    user=parsed.username,
                    password=parsed.password,
                    database=parsed.path.lstrip('/'),
                    port=parsed.port or 3306
                )
            else:
                # 2. Try individual Railway vars (preferred for visibility)
                # Handle variations like MYSQLHOST vs MYSQL_HOST, MYSQLDATABASE vs MYSQL_DATABASE
                host = os.getenv("MYSQLHOST") or os.getenv("MYSQL_HOST")
                user = os.getenv("MYSQLUSER") or os.getenv("MYSQL_USER")
                password = os.getenv("MYSQLPASSWORD") or os.getenv("MYSQL_PASSWORD")
                database = os.getenv("MYSQLDATABASE") or os.getenv("MYSQL_DATABASE")
                port = os.getenv("MYSQLPORT") or os.getenv("MYSQL_PORT")

                # 3. Fallback Logic
                # If we are on Railway, MYSQLHOST will usually be set. 
                # If host is "localhost", it's likely being loaded from a local .env file.
                if not host or host == "localhost":
                    print("DEBUG: No Railway host found (or localhost detected). Using fallback/local config.")
                    host = os.getenv("DB_HOST", "ballast.proxy.rlwy.net")
                    user = os.getenv("DB_USER", "root")
                    password = os.getenv("DB_PASSWORD", "FpLCHBsckikkzneiEOHlQAEakEHIaECS")
                    database = os.getenv("DB_NAME", "railway")
                    port = os.getenv("DB_PORT", "33613")
                
                port = int(port) if port else 3306
                
                print(f"DEBUG: Connecting via vars (Host: {host}, Port: {port}, User: {user})")
                
                g.db = mysql.connector.connect(
                    host=host,
                    user=user,
                    password=password,
                    database=database,
                    port=port
                )
        except mysql.connector.Error as err:
            if err.errno == 2003:
                print(f"Database Connection Error (2003): Connection Refused to '{host}:{port}'. "
                      "Check if MYSQLHOST, MYSQLPORT, etc. are set correctly in Railway.")
            else:
                print(f"Database Connection Error: {err}")
            return None
        except Exception as e:
            print(f"Unexpected Database Error: {e}")
            return None
    return g.db

def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


# Global cursor removed to prevent connection timeouts/errors
# Cursors should be created per request


# ---------------- GEMINI SETUP ----------------

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError(" GEMINI_API_KEY is missing! Please add it to your .env file.")

genai_client = genai.Client(api_key=GEMINI_API_KEY)

def analyze_patient_logs(logs_text: str):
    """Send logs to Gemini and return AI analysis (improving, worsening, stable)."""
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=logs_text)],
        ),
    ]

    try:
        ai_message = ""
        for chunk in genai_client.models.generate_content_stream(
            model="gemini-2.5-flash-lite",
            contents=contents,
        ):
            if chunk.text:
                ai_message += chunk.text
        return ai_message.strip()
    except Exception as e:
        print(" Gemini error:", e)
        return " Telemed AI is busy, please try again later."



# ---------------- FLASK APP ----------------
app = Flask(__name__)
app.secret_key = "123"
app.teardown_appcontext(close_db)

# ---------------- HOME ROUTE ----------------
@app.route("/")
def home():
        return render_template("base.html")


# ---------------- LOGIN ----------------
@app.route("/login", methods=["GET", "POST"])
def login():
    conn = get_db()
    if conn is None:
        flash("Database connection failed. Please try again later.", "danger")
        return render_template("login.html")
    
    cursor = conn.cursor(dictionary=True)
    if request.method == "POST":
        role = request.form["role"]
        email = request.form["email"]
        password = request.form["password"]

        if role == "patient":
            cursor.execute("SELECT * FROM patients WHERE email=%s AND password=%s", (email, password))
            user = cursor.fetchone()
            if user:
                session["patient_id"] = user["patient_id"]
                session["patient_name"] = user["name"]
                return redirect("/patient")

        if role == "doctor":
            cursor.execute("SELECT * FROM doctors WHERE email=%s AND password=%s", (email, password))
            doc = cursor.fetchone()
            if doc:
                session["doctor_id"] = doc["doctor_id"]
                session["doctor_name"] = doc["name"]
                return redirect("/doctor")

        flash("Invalid login credentials", "danger")
    return render_template("login.html")
   #---------------- PATIENT DASHBOARD ----------------
@app.route('/patient')
def patient():
    if 'patient_id' not in session:
        return redirect('/login')

    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    patient_id = session['patient_id']

    # -----------------------------
    # Ensure doctor is assigned
    # -----------------------------
    cursor.execute("""
        SELECT p.doctor_id, d.name AS doctor_name
        FROM patients p
        LEFT JOIN doctors d ON p.doctor_id = d.doctor_id
        WHERE p.patient_id = %s
    """, (patient_id,))
    pdata = cursor.fetchone()

    doctor_name = pdata['doctor_name'] if pdata else None

    if pdata and pdata['doctor_id'] is None:
        cursor.execute("SELECT doctor_id FROM doctors ORDER BY RAND() LIMIT 1")
        d = cursor.fetchone()
        if d:
            cursor.execute(
                "UPDATE patients SET doctor_id=%s WHERE patient_id=%s",
                (d['doctor_id'], patient_id)
            )
            conn.commit()

            cursor.execute(
                "SELECT name FROM doctors WHERE doctor_id=%s",
                (d['doctor_id'],)
            )
            doctor_name = cursor.fetchone()['name']

    # -----------------------------
    # Fetch appointment updates
    # Latest updates first
    # -----------------------------
    cursor.execute("""
        SELECT 
            a.id,
            a.date,
            a.status,
            a.update_reason,
            d.name AS doctor_name
        FROM appointments a
        LEFT JOIN doctors d ON a.doctor_id = d.doctor_id
        WHERE a.patient_id = %s
        ORDER BY a.updated_at DESC, a.date DESC
    """, (patient_id,))
    all_appointments = cursor.fetchall() or []

    pending = [a for a in all_appointments if a['status'] == 'pending'][:2]
    updates = [a for a in all_appointments if a['status'] != 'pending'][:5]

    appointments = updates + pending

    # -----------------------------
    # Health logs (last 5)
    # -----------------------------
    cursor.execute("""
        SELECT date, symptoms, medication
        FROM health_logs
        WHERE patient_id = %s
        ORDER BY date DESC
        LIMIT 5
    """, (patient_id,))
    logs = cursor.fetchall() or []

    # -----------------------------
    # AI analysis
    # -----------------------------
    if logs:
        history = "\n".join([f"{l['date']}: {l['symptoms']}" for l in logs])
        ai_message = analyze_patient_logs(
            f"Patient logs:\n{history}\nProvide a short supportive health insight."
        )
    else:
        ai_message = "No health logs yet. Start logging to receive AI insights."

    # -----------------------------
    # Doctor recommendations (Fetch ALL for trend)
    # -----------------------------
    cursor.execute("""
        SELECT r.advice, r.date, d.name AS doctor_name
        FROM recommendations r
        JOIN doctors d ON r.doctor_id = d.doctor_id
        WHERE r.patient_id = %s
        ORDER BY r.date DESC
    """, (patient_id,))
    all_recommendations = cursor.fetchall() or []

    # Filter for trend graph (must start with keyword)
    # Create a map of date string (YYYY-MM-DD) -> score
    rec_map = {}
    for rec in all_recommendations:
        advice_lower = rec['advice'].lower().strip()
        score = None
        if advice_lower.startswith("improving"):
            score = 8
        elif advice_lower.startswith("stable"):
            score = 5
        elif advice_lower.startswith("worsening"):
            score = 2
        
        # Only keep the latest for that day if not already set (since query is DESC)
        date_str = rec['date'].strftime("%Y-%m-%d")
        if score is not None and date_str not in rec_map:
            rec_map[date_str] = score

    # Generate last 7 days
    from datetime import timedelta
    today = datetime.now().date()
    labels = []
    trend_data = []
    
    # Iterate last 7 days
    for i in range(6, -1, -1):
        day = today - timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        
        # X-Axis Label: Day Name (e.g., Monday)
        labels.append(day.strftime("%A"))
        
        # Y-Axis Data: Score or None
        trend_data.append(rec_map.get(day_str, None))

    # Data for Display List (Latest 5)
    recommendations = all_recommendations[:5]

    # -----------------------------
    # Render patient dashboard
    # -----------------------------
    return render_template(
        'patient.html',
        patient_name=session.get('patient_name', 'Patient'),
        doctor_name=doctor_name,
        appointments=appointments,
        logs=logs,
        ai_message=ai_message,
        labels=labels,
        trend_data=trend_data,
        recommendations=recommendations
    )


@app.route("/appointment/edit/<int:appointment_id>", methods=["POST"])
def edit_appointment(appointment_id):
    if "doctor_id" not in session:
        return redirect("/login")
    
    conn = get_db()
    cur = conn.cursor(dictionary=True)

    new_date = request.form.get("new_date")
    reason = request.form.get("reason")

    if not new_date or not reason:
        flash("Date and reason are required.", "danger")
        return redirect(url_for("doctor_dashboard"))

    cur = conn.cursor(dictionary=True)

    # Fetch patient info
    cur.execute("""
        SELECT p.phone, p.name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        WHERE a.id = %s
    """, (appointment_id,))
    patient = cur.fetchone()

    # EDIT appointment (clean + safe)
    cur.execute("""
        UPDATE appointments
        SET
            date = %s,
            status = 'rescheduled',
            update_reason = %s,
            updated_at = NOW()
        WHERE id = %s
    """, (new_date, reason, appointment_id))
    conn.commit()
    cur.close()


    flash("Appointment updated successfully. Patient notified.", "warning")
    return redirect(url_for("doctor_dashboard"))



@app.route("/appointment/<int:appointment_id>/<action>", methods=["POST", "GET"])
def update_appointment(appointment_id, action):
    if "doctor_id" not in session:
        return redirect("/login")
    
    conn = get_db()

    if action not in ("accept", "reject"):
        flash("Invalid action", "warning")
        return redirect(url_for("doctor_dashboard"))

    new_status = "accepted" if action == "accept" else "rejected"

    cur = conn.cursor(dictionary=True)

    # Get patient info
    cur.execute("""
        SELECT p.phone, p.name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        WHERE a.id = %s
    """, (appointment_id,))
    patient = cur.fetchone()

    # Update appointment (CORRECT)
    cur.execute("""
        UPDATE appointments
        SET status = %s,
            updated_at = NOW()
        WHERE id = %s
    """, (new_status, appointment_id))
    conn.commit()

    cur.close()


    flash(f"Appointment {new_status}. Patient notified.", "success")
    return redirect(url_for("doctor_dashboard"))
# ---------------- DOCTOR DASHBOARD ----------------
@app.route("/doctor")
def doctor_dashboard():
    if "doctor_id" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    doctor_id = session["doctor_id"]
    doctor_name = session.get("doctor_name", "Specialist")

    # 1. Fetch Pending Appointments
    cursor.execute("""
        SELECT a.id, a.date, p.name AS patient_name
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        WHERE a.doctor_id = %s AND LOWER(a.status) = 'pending'
        ORDER BY a.date ASC
    """, (doctor_id,))
    appointments = cursor.fetchall()

    # 2. Fetch Assigned Patients
    cursor.execute("SELECT patient_id, name, email FROM patients WHERE doctor_id = %s", (doctor_id,))
    patients = cursor.fetchall()

    patient_logs = {}
    ai_summaries = {}

    # 3. Process Trends & Expert Advice
    for p in patients:
        p_id = p["patient_id"]
        
        # Get last 5 logs for historical context
        cursor.execute("""
            SELECT date, symptoms, medication 
            FROM health_logs
            WHERE patient_id = %s 
            ORDER BY date DESC LIMIT 5
        """, (p_id,))
        logs = cursor.fetchall()
        patient_logs[p_id] = logs

        if logs:
            # Build a history string for the AI Nephrologist persona
            history_str = " | ".join([f"{l['date']}: {l['symptoms']}" for l in logs])
            
            prompt = (
                f"As a Senior Nephrologist, analyze these 5 logs: {history_str}. "
                "Provide concrete clinical advice (not a summary) under 160 characters. "
                "Specify the most urgent action or monitoring requirement for this patient."
            )
            
            try:
                raw_advice = analyze_patient_logs(prompt)
                # Strip AI chatter to save space
                clean_advice = raw_advice.replace("Advice:", "").replace("Clinical Advice:", "").strip()
                ai_summaries[p_id] = (clean_advice[:157] + '..') if len(clean_advice) > 160 else clean_advice
            except Exception as e:
                print(f"AI Error: {e}")
                ai_summaries[p_id] = "AI analysis unavailable. Review logs manually."
        else:
            ai_summaries[p_id] = "No patient history found. Monitoring cannot be established."

    return render_template(
        "doctor.html",
        doctor_name=doctor_name,
        appointments=appointments,
        patients=patients,
        patient_logs=patient_logs,
        ai_summaries=ai_summaries
    )

# ---------------- LOG HEALTH ----------------
@app.route("/log", methods=["POST"])
def log():
    if "patient_id" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        INSERT INTO health_logs (patient_id, symptoms, medication)
        VALUES (%s, %s, %s)
    """, (session["patient_id"], request.form["symptoms"], request.form["medication"]))
    conn.commit()
    return redirect("/patient")


@app.route("/book", methods=["GET", "POST"])
def book_appointment():
    if "patient_id" not in session:
        flash("Please login first.", "danger")
        return redirect(url_for("login"))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT doctor_id FROM patients WHERE patient_id=%s", (session['patient_id'],))
    res = cursor.fetchone()

    if not res or not res['doctor_id']:
        flash("No doctor assigned yet. Please wait.", "warning")
        return redirect(url_for("patient"))

    if request.method == "POST":
        date_time = f"{request.form['date']} {request.form['time']}:00"
        cursor.execute("""
            INSERT INTO appointments (patient_id, doctor_id, date, status)
            VALUES (%s, %s, %s, 'pending')
        """, (session['patient_id'], res['doctor_id'], date_time))
        conn.commit()
        flash("Appointment requested successfully.", "success")
        return redirect(url_for("patient"))

    return render_template("book.html")

@app.route('/labs')
def labs_page():
    if 'patient_id' not in session:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM lab_results 
        WHERE patient_id=%s 
        ORDER BY date DESC
    """, (session['patient_id'],))
    
    results = cursor.fetchall() or []
    return render_template('labs.html', labs=results)

@app.route('/contact')
def contact_page():
    if 'patient_id' not in session:
        flash("Session expired. Please login again.", "danger")
        return redirect(url_for('login'))

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM doctors")
    docs = cursor.fetchall() or []
    return render_template('contact.html', doctors=docs)



@app.route("/recommend/<int:patient_id>", methods=["GET", "POST"])
def recommend(patient_id):
    if "doctor_id" not in session:
        return redirect("/login")

    conn = get_db()
    cursor = conn.cursor(dictionary=True)

    if request.method == "POST":
        advice = request.form["advice"]
        cursor.execute("""
            INSERT INTO recommendations (patient_id, doctor_id, advice, date)
            VALUES (%s, %s, %s, NOW())
        """, (patient_id, session["doctor_id"], advice))
        conn.commit()
        flash("Recommendation sent successfully!", "success")
        return redirect(url_for("doctor_dashboard"))

    cursor.execute("SELECT * FROM patients WHERE patient_id=%s", (patient_id,))
    patient = cursor.fetchone()
    return render_template("recommendation.html", patient=patient)







# ---------- ADMIN ----------
@app.route("/admin/dashboard")
def admin_dashboard():
    if "admin_id" not in session:
        return redirect("/login")
    return render_template("admin_dashboard.html", admin_name=session["admin_name"])


# Manage Patients
@app.route("/admin/patients")
def manage_patients():
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM patients")
    patients = cursor.fetchall()
    return render_template("manage_patients.html", patients=patients)


@app.route("/admin/patients/add", methods=["POST"])
def add_patient():
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    name = request.form["name"]
    email = request.form["email"]
    password = request.form["password"]
    cursor.execute("INSERT INTO patients (name, email, password) VALUES (%s, %s, %s)", (name, email, password))
    conn.commit()
    return redirect(url_for("manage_patients"))


@app.route("/admin/patients/delete/<int:id>", methods=["POST"])
def delete_patient(id):
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("DELETE FROM patients WHERE patient_id = %s", (id,))
    conn.commit()
    return redirect(url_for("manage_patients"))


# Manage Doctors
@app.route("/admin/doctors")
def manage_doctors():
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM doctors")
    doctors = cursor.fetchall()
    return render_template("manage_doctors.html", doctors=doctors)


@app.route("/admin/doctors/add", methods=["POST"])
def add_doctor():
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    name = request.form["name"]
    email = request.form["email"]
    password = request.form["password"]
    specialization = request.form["specialization"]
    cursor.execute("INSERT INTO doctors (name, email, password, specialization) VALUES (%s, %s, %s, %s)",
                   (name, email, password, specialization))
    conn.commit()
    return redirect(url_for("manage_doctors"))


@app.route("/admin/doctors/delete/<int:id>", methods=["POST"])
def delete_doctor(id):
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("DELETE FROM doctors WHERE doctor_id = %s", (id,))
    conn.commit()
    return redirect(url_for("manage_doctors"))


# Manage Caregivers
@app.route("/admin/caregivers")
def manage_caregivers():
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM caregivers")
    caregivers = cursor.fetchall()
    return render_template("manage_caregivers.html", caregivers=caregivers)


@app.route("/admin/caregivers/add", methods=["POST"])
def add_caregiver():
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    name = request.form["name"]
    email = request.form["email"]
    password = request.form["password"]
    cursor.execute("INSERT INTO caregivers (name, email, password) VALUES (%s, %s, %s)", (name, email, password))
    conn.commit()
    return redirect(url_for("manage_caregivers"))


@app.route("/admin/caregivers/delete/<int:id>", methods=["POST"])
def delete_caregiver(id):
    if "admin_id" not in session:
        return redirect("/login")
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("DELETE FROM caregivers WHERE caregiver_id = %s", (id,))
    conn.commit()
    return redirect(url_for("manage_caregivers"))




# ---------------- LOGOUT ----------------
@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

# ---------------- RUN ----------------
if __name__ == "__main__":
    load_dotenv() # Only load .env if running locally
    app.run(debug=True)
