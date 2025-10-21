from flask import Flask, render_template, request, redirect, session, flash, url_for, jsonify
import mysql.connector
import os
import africastalking
from google import genai
from google.genai import types
from dotenv import load_dotenv


load_dotenv()

# -------------------- DATABASE --------------------
try:
    conn = mysql.connector.connect(
        host="localhost",            
        port=3306,                   
        user="root",                 
        password="",                 
        database="telemed_system"    
    )
    cursor = conn.cursor(dictionary=True)
    print(" Connected to local MySQL successfully")
except mysql.connector.Error as err:
    print(" Error connecting to MySQL:", err)
    exit()


username = "sandbox"   
api_key = "atsk_ff62bffca1190c7c11dfa010401b3fd68de662c2829e11245ac00996fa144347be477ab2"

def send_sms(recipient, message):
    africastalking.initialize(username, api_key)
    sms = africastalking.SMS
    try:
        response = sms.send(message, [recipient])
        print("SMS sent:", response)
    except Exception as e:
        print(f"Failed to send SMS: {str(e)}")



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



app = Flask(__name__)
app.secret_key = '123'


@app.route('/')
def home():
    return render_template('base.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form.get('role')  
        email = request.form['email']
        password = request.form['password']

        
        cursor.execute("SELECT * FROM admins WHERE email = %s AND password = %s", (email, password))
        admin = cursor.fetchone()
        if admin:
            session['admin_id'] = admin['admin_id']

            session['admin_name'] = admin['name']
            return redirect('/admin/dashboard')

    
        if role in ['patient', 'caregiver']:
            cursor.execute("SELECT * FROM patients WHERE email = %s AND password = %s", (email, password))
            user = cursor.fetchone()
            if user:
                session['patient_id'] = user['patient_id']
                session['patient_name'] = user['name']
                session['is_caregiver'] = (role == 'caregiver')

                # Auto-assign a doctor if none
                if not user.get("doctor_id"):
                    cursor.execute("SELECT doctor_id FROM doctors ORDER BY RAND() LIMIT 1")
                    doctor = cursor.fetchone()
                    if doctor:
                        cursor.execute("UPDATE patients SET doctor_id = %s WHERE patient_id = %s",
                                       (doctor['doctor_id'], user['patient_id']))
                        conn.commit()

                return redirect('/patient')
            else:
                flash('Invalid credentials for patient/caregiver.')

        elif role == 'doctor':
            cursor.execute("SELECT * FROM doctors WHERE email = %s AND password = %s", (email, password))
            user = cursor.fetchone()
            if user:
                session['doctor_id'] = user['doctor_id']
                session['doctor_name'] = user['name']
                return redirect(url_for('doctor_dashboard'))
            else:
                flash('Invalid doctor credentials.')

    return render_template('login.html')



@app.route('/patient')
def patient():
    if 'patient_id' not in session:
        return redirect('/login')

    # Get last 5 logs (most recent first)
    cursor.execute("""SELECT date, symptoms, medication 
                      FROM health_logs 
                      WHERE patient_id = %s 
                      ORDER BY date DESC LIMIT 5""", (session['patient_id'],))
    logs = cursor.fetchall()

    # Convert logs to readable history for AI
    history = "\n".join([f"{log['date']}: {log['symptoms']} (med: {log['medication']})"
                         for log in logs]) if logs else "No logs available."

    # AI prompt
    prompt = f"""
    You are a nephrologist AI reviewing a dialysis patient's recent logs.

    Patient’s last 5 logs:
    {history}

    Task:
    1. Classify the patient’s overall condition as Improving, Stable, or Worsening.
    2. Give exactly one short medical suggestion (≤160 characters).
    3. Respond in this format:

    Condition: <Improving/Stable/Worsening>
    Advice: <short suggestion>
    """
    ai_message = analyze_patient_logs(prompt)

    # --- Generate trend data dynamically from logs ---
    trend_data = []
    labels = []

    if logs:
        for log in reversed(logs):  # chronological order (oldest → newest)
            labels.append(log['date'].strftime("%Y-%m-%d"))

            # crude scoring: fewer symptoms = higher score
            symptoms_text = log['symptoms'] or ""
            score = max(1, 10 - min(len(symptoms_text.split()), 9))  
            trend_data.append(score)
    else:
        trend_data = []
        labels = []

    # Doctor’s advice
    cursor.execute("SELECT advice FROM recommendations WHERE patient_id = %s ORDER BY date DESC LIMIT 1",
                   (session['patient_id'],))
    rec = cursor.fetchone()

    cursor.execute("""
        SELECT r.advice, r.date, d.name AS doctor_name 
        FROM recommendations r
        JOIN doctors d ON r.doctor_id = d.doctor_id
        WHERE r.patient_id = %s 
        ORDER BY r.date DESC LIMIT 5
    """, (session['patient_id'],))
    recommendations = cursor.fetchall()

    doctor_advice = rec['advice'] if rec else "No recent doctor advice."

    return render_template('patient.html',
                           patient_name=session['patient_name'],
                           logs=logs,
                           ai_message=ai_message,
                           doctor_advice=doctor_advice,
                           trend_data=trend_data,
                           labels=labels,
                           recommendations=recommendations)


@app.route('/log', methods=['POST'])
def log_status():
    if 'patient_id' not in session:
        return redirect('/login')

    symptoms = request.form['symptoms']
    medication = request.form['medication']

    cursor.execute("INSERT INTO health_logs (patient_id, symptoms, medication) VALUES (%s, %s, %s)",
                   (session['patient_id'], symptoms, medication))
    conn.commit()
    flash("Health status logged successfully.")
    return redirect('/patient')


@app.route('/doctor')
def doctor_dashboard():
    if 'doctor_id' not in session:
        return redirect('/login')

    doctor_id = session['doctor_id']

    # Fetch patients assigned to this doctor
    cursor.execute("SELECT p.patient_id, p.name, p.email FROM patients p WHERE p.doctor_id = %s", (doctor_id,))
    patients = cursor.fetchall()

    patient_logs, ai_summaries = {}, {}

    for p in patients:
       
        cursor.execute("""
            SELECT date, symptoms, medication 
            FROM health_logs 
            WHERE patient_id = %s 
            ORDER BY date DESC LIMIT 5
        """, (p['patient_id'],))
        logs = cursor.fetchall()
        patient_logs[p['patient_id']] = logs

        if logs:
          
            logs_text = "\n".join([f"{log['date']}: {log['symptoms']} (med: {log['medication']})" for log in logs])

            # Precise AI suggestion for doctor
            prompt = f"""
    ;;        You are a nephrologist AI reviewing a dialysis patient's recent logs.

            Patient’s last 5 logs:
            {logs_text}

            Task:
            
            1. Provide a precise, actionable recommendation for the doctor in 2-3 sentences.
            2. Respond in this format:
             <precise recommendation>
            """

            ai_summaries[p['patient_id']] = analyze_patient_logs(prompt)
        else:
            ai_summaries[p['patient_id']] = "No recent logs. Recommend follow-up."

    # Fetch pending appointments
    cursor.execute("""
        SELECT a.id, a.date, p.name AS patient_name 
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        WHERE a.doctor_id = %s AND a.status = 'pending'
        ORDER BY a.date ASC
    """, (doctor_id,))
    appointments = cursor.fetchall()

    return render_template('doctor.html',
                           doctor_name=session['doctor_name'],
                           patients=patients,
                           patient_logs=patient_logs,
                           ai_summaries=ai_summaries,
                           appointments=appointments)


@app.route("/ai_suggest/<int:patient_id>")
def ai_suggest(patient_id):
    """Return a longer AI suggestion for the doctor (via modal in doctor.html)."""
    cursor.execute("""SELECT date, symptoms, medication 
                      FROM health_logs 
                      WHERE patient_id = %s 
                      ORDER BY date DESC LIMIT 5""", (patient_id,))
    logs = cursor.fetchall()

    if logs:
        logs_text = "\n".join([f"{log['date']}: {log['symptoms']} (med: {log['medication']})" for log in logs])
        suggestion = analyze_patient_logs(
            f"Recent logs:\n{logs_text}\nProvide clinical decision support suggestions in 3-4 sentences."
        )
    else:
        suggestion = "No recent logs. Recommend scheduling a follow-up."

    return jsonify({"suggestion": suggestion})


@app.route("/recommend/<int:patient_id>", methods=["GET", "POST"])
def recommend(patient_id):
    if "doctor_id" not in session:
        return redirect("/login")

    if request.method == "POST":
        advice = request.form["advice"]
        cursor.execute("INSERT INTO recommendations (patient_id, doctor_id, advice) VALUES (%s, %s, %s)",
                       (patient_id, session["doctor_id"], advice))
        conn.commit()
        flash("Recommendation saved successfully!")
        return redirect("/doctor")

    cursor.execute("SELECT * FROM patients WHERE patient_id = %s", (patient_id,))
    patient = cursor.fetchone()

    cursor.execute("SELECT * FROM health_logs WHERE patient_id = %s ORDER BY date DESC LIMIT 5", (patient_id,))
    logs = cursor.fetchall()

    return render_template("recomendation.html", patient=patient, logs=logs)

from flask import flash, redirect, url_for

@app.route("/appointment/<int:appointment_id>/<action>", methods=["POST", "GET"])
def update_appointment(appointment_id, action):
    if "doctor_id" not in session:
        return redirect("/login")

    try:
        # use a fresh cursor for each DB operation
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT patient_id FROM appointments WHERE id=%s", (appointment_id,))
        appt = cur.fetchone()
        cur.close()

        if not appt:
            flash("Appointment not found ❌", "danger")
            return redirect(url_for("doctor_dashboard"))

        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT phone, name FROM patients WHERE patient_id=%s", (appt["patient_id"],))
        patient = cur.fetchone()
        cur.close()

        if not patient or not patient.get("phone"):
            flash("Patient phone not found ❌", "danger")
            return redirect(url_for("doctor_dashboard"))

        if action not in ("accept", "reject"):
            flash("Invalid action ❌", "warning")
            return redirect(url_for("doctor_dashboard"))

        new_status = "accepted" if action == "accept" else "rejected"
        sms_message = f"Hello {patient.get('name','')}, your appointment has been {new_status.upper()} {'✅' if action=='accept' else '❌'}"

      
        cur = conn.cursor()
        cur.execute("UPDATE appointments SET status=%s WHERE id=%s", (new_status, appointment_id))
        conn.commit()
        cur.close()


        try:
            send_sms(patient["phone"], sms_message)
        except Exception as e:
            app.logger.exception("SMS sending failed (but appointment already updated)")

        flash(f"Appointment {new_status}. Patient notified (if SMS succeeded).", "success" if action=="accept" else "danger")

        
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"message": f"Appointment {new_status}", "status": "ok"})

        return redirect(url_for("doctor_dashboard"))

    except Exception as e:
        app.logger.exception("Error updating appointment")
        flash("Error updating appointment. See server log for details.", "danger")
        return redirect(url_for("doctor_dashboard"))


@app.route("/appointments/accept/<int:app_id>")
def accept_appointment(app_id):
   
    cursor.execute("UPDATE appointments SET status='accepted' WHERE id=%s", (app_id,))
    conn.commit()

    # Fetch patient info
    cursor.execute("""
        SELECT p.phone, p.name, a.date 
        FROM appointments a 
        JOIN patients p ON a.patient_id = p.patient_id 
        WHERE a.id=%s
    """, (app_id,))
    appointment = cursor.fetchone()

    if appointment and appointment["phone"]:
        msg = f"Hello {appointment['name']}, your appointment on {appointment['date']} has been ACCEPTED ✅."
        send_sms(appointment["phone"], msg)

    flash("✅ Appointment accepted. Patient notified via SMS.")
    return redirect(url_for("doctor_dashboard"))



@app.route("/appointments/reject/<int:app_id>")
def reject_appointment(app_id):
    cursor.execute("UPDATE appointments SET status='rejected' WHERE id=%s", (app_id,))
    conn.commit()

    cursor.execute("""
        SELECT p.phone, p.name, a.date 
        FROM appointments a 
        JOIN patients p ON a.patient_id = p.patient_id 
        WHERE a.id=%s
    """, (app_id,))
    appointment = cursor.fetchone()

    if appointment and appointment["phone"]:
        message = f"Hello {appointment['name']}, your appointment on {appointment['date']} has been REJECTED ❌. Please reschedule."
        send_sms(appointment["phone"], message)

    flash("❌ Appointment rejected. Patient notified via SMS.")
    return redirect(url_for("doctor_dashboard"))





@app.route("/book", methods=["GET", "POST"])
def book_appointment():
    if "patient_id" not in session:
        return redirect("/login")

    cursor.execute("SELECT doctor_id FROM patients WHERE patient_id = %s", (session["patient_id"],))
    assigned = cursor.fetchone()
    if not assigned or not assigned["doctor_id"]:
        flash("No doctor assigned yet. Please contact admin.")
        return redirect("/patient")

    doctor_id = assigned["doctor_id"]

    if request.method == "POST":
        date = request.form["date"]
        time = request.form["time"]
        cursor.execute("INSERT INTO appointments (patient_id, doctor_id, date, time, status) VALUES (%s, %s, %s, %s, 'pending')",
                       (session["patient_id"], doctor_id, date, time))
        conn.commit()
        flash("Appointment booked successfully with your assigned doctor!")
        return redirect("/patient")

    cursor.execute("SELECT name, specialization FROM doctors WHERE doctor_id = %s", (doctor_id,))
    doctor = cursor.fetchone()
    return render_template("book.html", doctor=doctor)


@app.route("/contact")
def contact():
    cursor.execute("SELECT doctor_id, name, specialization, phone, email FROM doctors")
    doctors = cursor.fetchall()

    cursor.execute("SELECT nurse_id, name, specialization, phone, email FROM nurses")
    nurses = cursor.fetchall()

    return render_template("contact.html", doctors=doctors, nurses=nurses)


@app.route('/message_care_team', methods=['POST'])
def message_care_team():
    if 'patient_id' not in session:
        return redirect('/login')

    cursor.execute("INSERT INTO messages (patient_id, recipient_id, subject, body) VALUES (%s, %s, %s, %s)",
                   (session['patient_id'], request.form['recipient_id'],
                    request.form['subject'], request.form['message']))
    conn.commit()
    flash('Message sent securely to your care team.')
    return redirect('/contact')


@app.route('/labs')
def labs_page():
    if 'patient_id' not in session:
        return redirect('/login')

    # Get patient profile (age, gender, vitals)
    cur1 = conn.cursor(dictionary=True, buffered=True)
    cur1.execute("""
        SELECT name, age, gender, blood_sugar, blood_pressure, weight, urine_output
        FROM patients 
        WHERE patient_id = %s
    """, (session['patient_id'],))
    patient = cur1.fetchone()
    cur1.close()

    # Get lab results
    cur2 = conn.cursor(dictionary=True, buffered=True)
    cur2.execute("""
        SELECT id, DATE_FORMAT(date, '%%Y-%%m-%%d') AS date, test_name, value, unit,
               reference_range, status, clinician_notes
        FROM lab_results
        WHERE patient_id = %s
        ORDER BY date DESC, id DESC
    """, (session['patient_id'],))
    labs = cur2.fetchall() or []
    cur2.close()

    unique_tests = sorted({row['test_name'] for row in labs})
    return render_template('labs.html', patient=patient, labs=labs, unique_tests=unique_tests)








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
    cursor.execute("SELECT * FROM patients")
    patients = cursor.fetchall()
    return render_template("manage_patients.html", patients=patients)


@app.route("/admin/patients/add", methods=["POST"])
def add_patient():
    if "admin_id" not in session:
        return redirect("/login")
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
    cursor.execute("DELETE FROM patients WHERE patient_id = %s", (id,))
    conn.commit()
    return redirect(url_for("manage_patients"))


# Manage Doctors
@app.route("/admin/doctors")
def manage_doctors():
    if "admin_id" not in session:
        return redirect("/login")
    cursor.execute("SELECT * FROM doctors")
    doctors = cursor.fetchall()
    return render_template("manage_doctors.html", doctors=doctors)


@app.route("/admin/doctors/add", methods=["POST"])
def add_doctor():
    if "admin_id" not in session:
        return redirect("/login")
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
    cursor.execute("DELETE FROM doctors WHERE doctor_id = %s", (id,))
    conn.commit()
    return redirect(url_for("manage_doctors"))


# Manage Caregivers
@app.route("/admin/caregivers")
def manage_caregivers():
    if "admin_id" not in session:
        return redirect("/login")
    cursor.execute("SELECT * FROM caregivers")
    caregivers = cursor.fetchall()
    return render_template("manage_caregivers.html", caregivers=caregivers)


@app.route("/admin/caregivers/add", methods=["POST"])
def add_caregiver():
    if "admin_id" not in session:
        return redirect("/login")
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
    cursor.execute("DELETE FROM caregivers WHERE caregiver_id = %s", (id,))
    conn.commit()
    return redirect(url_for("manage_caregivers"))



@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# -------------------- MAIN --------------------
if __name__ == '__main__':
    app.run(debug=True)
