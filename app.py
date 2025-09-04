from flask import Flask, render_template, request, redirect, session, flash, url_for
import mysql.connector
import os
from google import genai
from google.genai import types
from dotenv import load_dotenv 


load_dotenv() 

# -------------------- GEMINI CONFIG --------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    raise ValueError("❌ GEMINI_API_KEY is missing! Please add it to your .env file.")

genai_client = genai.Client(api_key=GEMINI_API_KEY)

def analyze_patient_logs(logs_text: str):
    """Send logs to Gemini and return AI analysis (improving, worsening, stable)."""
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=logs_text)],
        ),
    ]

    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    try:
        ai_message = ""
        for chunk in genai_client.models.generate_content_stream(
            model="gemini-2.5-flash-lite",
            contents=contents,
            config=config,
        ):
            if chunk.text:
                ai_message += chunk.text
        return ai_message.strip()
    except Exception as e:
        if "429" in str(e) or "quota" in str(e).lower():
            return "⚠️ Telemed AI is busy, please try again later."
        return "⚠️ Telemed AI is busy, please try again later."


app = Flask(__name__)
app.secret_key = '123'   


#DATABASE
try:
    conn = mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='telemed_system'
    )
    cursor = conn.cursor(dictionary=True)
except mysql.connector.Error as err:
    print("❌ Error connecting to MySQL:", err)
    exit()


# -------------------- ROUTES --------------------
@app.route('/')
def home():
    return render_template('base.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form['role']
        email = request.form['email']
        password = request.form['password']

        # Patient or caregiver login
        if role in ['patient', 'caregiver']:
            cursor.execute("SELECT * FROM patients WHERE email = %s AND password = %s", (email, password))
            user = cursor.fetchone()
            if user:
                session['patient_id'] = user['patient_id']
                session['patient_name'] = user['name']
                session['is_caregiver'] = (role == 'caregiver')

                # Auto-assign doctor if none is assigned
                if not user.get("doctor_id"):
                    cursor.execute("SELECT doctor_id FROM doctors ORDER BY RAND() LIMIT 1")
                    doctor = cursor.fetchone()
                    if doctor:
                        cursor.execute("""
                            UPDATE patients SET doctor_id = %s WHERE patient_id = %s
                        """, (doctor['doctor_id'], user['patient_id']))
                        conn.commit()

                return redirect('/patient')
            else:
                flash('Invalid credentials for patient.')

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

    # Fetch last 5 logs
    cursor.execute("""
        SELECT date, symptoms, medication 
        FROM health_logs 
        WHERE patient_id = %s 
        ORDER BY date DESC LIMIT 5
    """, (session['patient_id'],))
    logs = cursor.fetchall()

    history = "\n".join([
        f"{log['date']}: {log['symptoms']} (med: {log['medication']})"
        for log in logs
    ]) if logs else "No logs available."

    # Gemini AI analysis
    prompt = f"""
    You are a nephrologist AI reviewing a dialysis patient's recent logs.

    Patient’s last 5 logs:
    {history}

    Task:
    1. Classify the patient’s overall condition as You're Improving, You'reStable, or You're Worsening.
    2. Give exactly one short medical suggestion (≤160 characters).
    3. Respond in this exact format:

    Condition: <Improving/Stable/Worsening>
    Advice: <short suggestion>
    """

    ai_message = analyze_patient_logs(prompt)

    # Trend graph logic
    trend_data = [7, 7, 7, 7, 7, 7, 7]
    if "improving" in ai_message.lower():
        trend_data = [6.5, 6.7, 7.0, 7.2, 7.5, 7.7, 8.0]
    elif "worsening" in ai_message.lower():
        trend_data = [8.0, 7.7, 7.5, 7.2, 7.0, 6.8, 6.5]

    # Doctor advice
    cursor.execute("""
        SELECT advice 
        FROM recommendations 
        WHERE patient_id = %s 
        ORDER BY date DESC LIMIT 1
    """, (session['patient_id'],))
    rec = cursor.fetchone()
    doctor_advice = rec['advice'] if rec else "No recent doctor advice."

    return render_template(
        'patient.html',
        patient_name=session['patient_name'],
        logs=logs,
        ai_message=ai_message,
        doctor_advice=doctor_advice,
        trend_data=trend_data
    )


@app.route('/log', methods=['POST'])
def log_status():
    if 'patient_id' not in session:
        return redirect('/login')

    symptoms = request.form['symptoms']
    medication = request.form['medication']

    cursor.execute("""
        INSERT INTO health_logs (patient_id, symptoms, medication)
        VALUES (%s, %s, %s)
    """, (session['patient_id'], symptoms, medication))
    conn.commit()

    flash("Health status logged successfully.")
    return redirect('/patient')


@app.route('/doctor')
def doctor_dashboard():
    if 'doctor_id' not in session:
        return redirect('/login')

    doctor_id = session['doctor_id']

    # Patients assigned to doctor
    cursor.execute("""
        SELECT p.patient_id, p.name, p.email
        FROM patients p
        WHERE p.doctor_id = %s
    """, (doctor_id,))
    patients = cursor.fetchall()

    patient_logs = {}
    ai_summaries = {}

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
            logs_text = "\n".join(
                [f"{log['date']}: Symptoms={log['symptoms']}, Medication={log['medication']}" for log in logs]
            )
            prompt = f"""
            Last 5 logs for CKD patient:\n{logs_text}\n
            In <160 characters, say if the patient is improving, stable, or worsening.
            """
            ai_summaries[p['patient_id']] = analyze_patient_logs(prompt)

    # Pending appointments
    cursor.execute("""
        SELECT a.id, a.date, p.name AS patient_name 
        FROM appointments a
        JOIN patients p ON a.patient_id = p.patient_id
        WHERE a.doctor_id = %s AND a.status = 'pending'
        ORDER BY a.date ASC
    """, (doctor_id,))
    appointments = cursor.fetchall()

    return render_template(
        'doctor.html',
        doctor_name=session['doctor_name'],
        patients=patients,
        patient_logs=patient_logs,
        ai_summaries=ai_summaries,
        appointments=appointments
    )


@app.route("/recommend/<int:patient_id>", methods=["GET", "POST"])
def recommend(patient_id):
    if "doctor_id" not in session:
        return redirect("/login")

    if request.method == "POST":
        advice = request.form["advice"]

        cursor.execute("""
            INSERT INTO recommendations (patient_id, doctor_id, advice)
            VALUES (%s, %s, %s)
        """, (patient_id, session["doctor_id"], advice))
        conn.commit()

        flash("Recommendation saved successfully!")
        return redirect("/doctor")

    cursor.execute("SELECT * FROM patients WHERE patient_id = %s", (patient_id,))
    patient = cursor.fetchone()

    cursor.execute("""
        SELECT * FROM health_logs 
        WHERE patient_id = %s ORDER BY date DESC LIMIT 5
    """, (patient_id,))
    logs = cursor.fetchall()

    return render_template("recomendation.html", patient=patient, logs=logs)



@app.route("/book", methods=["GET", "POST"])
def book_appointment():
    if "patient_id" not in session:
        return redirect("/login")

    # Get the patient's assigned doctor
    cursor.execute("SELECT doctor_id FROM patients WHERE patient_id = %s", (session["patient_id"],))
    assigned = cursor.fetchone()
    if not assigned or not assigned["doctor_id"]:
        flash("No doctor assigned yet. Please contact admin.")
        return redirect("/patient")

    doctor_id = assigned["doctor_id"]

    if request.method == "POST":
        date = request.form["date"]
        time = request.form["time"]

        cursor.execute("""
            INSERT INTO appointments (patient_id, doctor_id, date, time, status)
            VALUES (%s, %s, %s, %s, 'pending')
        """, (session["patient_id"], doctor_id, date, time))
        conn.commit()

        flash("Appointment booked successfully with your assigned doctor!")
        return redirect("/patient")

    # Get doctor details for display
    cursor.execute("SELECT name, specialization FROM doctors WHERE doctor_id = %s", (doctor_id,))
    doctor = cursor.fetchone()

    return render_template("book.html", doctor=doctor)



@app.route("/contact")
def contact():
    cursor = conn.cursor(dictionary=True)

    # Fetch doctors
    cursor.execute("SELECT doctor_id, name, specialization, phone, email FROM doctors")
    doctors = cursor.fetchall()

    # Fetch nurses
    cursor.execute("SELECT nurse_id, name, specialization, phone, email FROM nurses")
    nurses = cursor.fetchall()

    # (Optional) Fetch other care team members (e.g., nutritionists, counselors)
    # cursor.execute("SELECT * FROM staff WHERE role='nutritionist'")
    # nutritionists = cursor.fetchall()

    cursor.close()
    return render_template("contact.html", doctors=doctors, nurses=nurses)


@app.route('/message_care_team', methods=['POST'])
def message_care_team():
    if 'patient_id' not in session:
        return redirect('/login')

    recipient_id = request.form['recipient_id']
    subject = request.form['subject']
    message = request.form['message']

    cursor.execute("""
        INSERT INTO messages (patient_id, recipient_id, subject, body)
        VALUES (%s, %s, %s, %s)
    """, (session['patient_id'], recipient_id, subject, message))
    conn.commit()
    flash('Message sent securely to your care team.')
    return redirect('/contact')



@app.route('/labs')
def labs_page():
    if 'patient_id' not in session:
        return redirect('/login')

    cursor.execute("""
        SELECT id, DATE_FORMAT(date, '%%Y-%%m-%%d') AS date, test_name, value, unit,
               reference_range, status, clinician_notes
        FROM lab_results
        WHERE patient_id = %s
        ORDER BY date DESC, id DESC
    """, (session['patient_id'],))
    labs = cursor.fetchall() or []

    # Build unique list of tests for the filter
    unique_tests = sorted({row['test_name'] for row in labs})

    return render_template('labs.html', labs=labs, unique_tests=unique_tests)




@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


# -------------------- MAIN --------------------
if __name__ == '__main__':
    app.run(debug=True)
