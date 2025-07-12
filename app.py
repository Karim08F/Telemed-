from flask import Flask, render_template, request, redirect, session, flash, url_for
import mysql.connector

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'  # Replace with a strong secret key

# MySQL Configuration
try:
    conn = mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='telemed_system'
    )
    cursor = conn.cursor(dictionary=True)
except mysql.connector.Error as err:
    print("Error connecting to MySQL:", err)
    exit()

# ROUTES

@app.route('/')
def home():
    return redirect('/login')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        role = request.form['role']
        email = request.form['email']
        password = request.form['password']

        if role in ['patient', 'caregiver']:
            cursor.execute("SELECT * FROM patients WHERE email = %s AND password = %s", (email, password))
            user = cursor.fetchone()
            if user:
                session['patient_id'] = user['patient_id']
                session['patient_name'] = user['name']
                session['is_caregiver'] = (role == 'caregiver')
                return redirect('/patient')
            else:
                flash('Invalid credentials for patient.')

        elif role == 'doctor':
            cursor.execute("SELECT * FROM doctors WHERE email = %s AND password = %s", (email, password))
            user = cursor.fetchone()
            if user:
                session['doctor_id'] = user['doctor_id']
                session['doctor_name'] = user['name']
                return redirect('/doctor')
            else:
                flash('Invalid doctor credentials.')

    return render_template('login.html')

@app.route('/patient')
def patient():
    if 'patient_id' not in session:
        return redirect('/login')

    cursor.execute("""
        SELECT * FROM health_logs WHERE patient_id = %s ORDER BY date DESC LIMIT 7
    """, (session['patient_id'],))
    logs = cursor.fetchall()

    
    ai_message = "You're doing okay, but please maintain your medication schedule."
    doctor_advice = "Continue with your current treatment and stay hydrated."

    return render_template('patient.html', patient_name=session['patient_name'], logs=logs, ai_message=ai_message, doctor_advice=doctor_advice)

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

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

if __name__ == '__main__':
    app.run(debug=True)
