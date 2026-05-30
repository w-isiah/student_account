import pytz
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from jinja2 import TemplateNotFound
from werkzeug.security import generate_password_hash

from apps import get_db_connection
from apps.user_students import blueprint  # Ensure this matches your blueprint registration

# --- Helpers ---

def get_kampala_time():
    return datetime.now(pytz.timezone("Africa/Kampala"))

def get_segment(request):
    try:
        segment = request.path.split('/')[-1]
        return segment if segment != '' else 'manage_students'
    except Exception:
        return None

# ---------------------------------------------------------
# Manage Student Accounts
# ---------------------------------------------------------

@blueprint.route('/manage_students')
def manage_students():
    try:
        with get_db_connection() as connection:
            with connection.cursor(dictionary=True) as cursor:

                # 1. STATISTICS
                cursor.execute("""
                    SELECT 
                        COUNT(id) AS total_students,
                        SUM(CASE WHEN account_status = 'Active' THEN 1 ELSE 0 END) AS active_count,
                        SUM(CASE WHEN gender = 'Female' THEN 1 ELSE 0 END) AS female_count,
                        SUM(CASE WHEN gender = 'Male' THEN 1 ELSE 0 END) AS male_count
                    FROM users_students
                """)
                stats = cursor.fetchone() or {
                    "total_students": 0,
                    "active_count": 0,
                    "female_count": 0,
                    "male_count": 0
                }

                # Safe fallback handling for SUM returning None on empty tables
                for key in ["active_count", "female_count", "male_count"]:
                    if stats[key] is None:
                        stats[key] = 0

                # 2. STUDENT LIST (LEFT JOIN with your dedicated student_next_of_kin table)
                cursor.execute("""
                    SELECT 
                        s.id, s.reg_no, s.first_name, s.last_name, s.other_name, 
                        s.email, s.gender, s.account_status, s.created_at,
                        nok.full_name AS nok_name, 
                        nok.relationship AS nok_relationship, 
                        nok.phone_primary AS nok_phone, 
                        nok.phone_secondary AS nok_phone_secondary, 
                        nok.email AS nok_email, 
                        nok.residential_address AS nok_address
                    FROM users_students s
                    LEFT JOIN student_next_of_kin nok ON s.id = nok.student_id
                    ORDER BY s.created_at DESC
                """)
                students = cursor.fetchall()

        return render_template(
            "user_students/student_list.html",
            stats=stats,
            students=students,
            segment="manage_students"
        )

    except Exception as e:
        flash(f"Error loading student data: {str(e)}", "danger")
        return redirect(url_for('home_blueprint.index'))

# ---------------------------------------------------------
# Add Student Account (Manual)
# ---------------------------------------------------------

@blueprint.route('/add_student', methods=['POST'])
def add_student():
    reg_no = request.form.get("reg_no", "").strip()
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    other_name = request.form.get("other_name", "").strip()
    email = request.form.get("email", "").strip()
    gender = request.form.get("gender")
    
    if not reg_no or not first_name or not last_name:
        flash("Registration Number, First Name, and Last Name are required.", "warning")
        return redirect(url_for("students_blueprint.manage_students"))

    hashed_password = generate_password_hash(reg_no)

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Unique constraints verification block
                cursor.execute("SELECT id FROM users_students WHERE reg_no = %s OR (email = %s AND email != '')", (reg_no, email))
                if cursor.fetchone():
                    flash("Duplicate Error: A student with this Registration Number or Email already exists.", "warning")
                    return redirect(url_for("students_blueprint.manage_students"))

                cursor.execute("""
                    INSERT INTO users_students
                    (reg_no, first_name, last_name, other_name, email, password, gender, account_status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'Active')
                """, (
                    reg_no, first_name, last_name, other_name, 
                    email if email else None, hashed_password, gender
                ))
                connection.commit()
                flash(f"Account for {reg_no} created successfully. Password is set to Registration Number.", "success")
                
    except Exception as e:
        flash(f"Database Error: {str(e)}", "danger")

    return redirect(url_for("students_blueprint.manage_students"))

# ---------------------------------------------------------
# Edit Student Account
# ---------------------------------------------------------

@blueprint.route('/edit_student/<int:student_id>', methods=['POST'])
def edit_student(student_id):
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    other_name = request.form.get("other_name", "").strip()
    email = request.form.get("email", "").strip()
    gender = request.form.get("gender")
    status = request.form.get("account_status")

    if not first_name or not last_name:
        flash("First Name and Last Name are required.", "warning")
        return redirect(url_for("students_blueprint.manage_students"))

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                if email:
                    cursor.execute("SELECT id FROM users_students WHERE email = %s AND id != %s", (email, student_id))
                    if cursor.fetchone():
                        flash("Error: This email address is already in use by another student.", "warning")
                        return redirect(url_for("students_blueprint.manage_students"))

                cursor.execute("""
                    UPDATE users_students
                    SET first_name=%s, last_name=%s, other_name=%s, 
                        email=%s, gender=%s, account_status=%s
                    WHERE id=%s
                """, (first_name, last_name, other_name, email if email else None, gender, status, student_id))
                
                connection.commit()
                flash("Student record updated successfully.", "success")
                
    except Exception as e:
        flash(f"Error updating record: {str(e)}", "danger")

    return redirect(url_for("students_blueprint.manage_students"))

# ---------------------------------------------------------
# Update Next of Kin Contact Details
# ---------------------------------------------------------

@blueprint.route('/update_nok/<int:student_id>', methods=['POST'])
def update_nok(student_id):
    nok_name = request.form.get("nok_name", "").strip()
    nok_relationship = request.form.get("nok_relationship")
    nok_phone = request.form.get("nok_phone", "").strip()
    nok_phone_secondary = request.form.get("nok_phone_secondary", "").strip()
    nok_email = request.form.get("nok_email", "").strip()
    nok_address = request.form.get("nok_address", "").strip()

    if not nok_name or not nok_relationship or not nok_phone:
        flash("Next of Kin Name, Relationship, and Primary Phone are required fields.", "warning")
        return redirect(url_for("students_blueprint.manage_students"))

    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Check if a NOK record already exists for this student
                cursor.execute("SELECT id FROM student_next_of_kin WHERE student_id = %s", (student_id,))
                existing_record = cursor.fetchone()

                if existing_record:
                    # UPDATE existing record matching your schema column fields
                    cursor.execute("""
                        UPDATE student_next_of_kin
                        SET full_name=%s, relationship=%s, phone_primary=%s,
                            phone_secondary=%s, email=%s, residential_address=%s
                        WHERE student_id=%s
                    """, (
                        nok_name, nok_relationship, nok_phone,
                        nok_phone_secondary if nok_phone_secondary else None,
                        nok_email if nok_email else None,
                        nok_address if nok_address else None,
                        student_id
                    ))
                else:
                    # INSERT a brand new record if it doesn't exist yet
                    cursor.execute("""
                        INSERT INTO student_next_of_kin 
                        (student_id, full_name, relationship, phone_primary, phone_secondary, email, residential_address, is_primary_contact)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 1)
                    """, (
                        student_id, nok_name, nok_relationship, nok_phone,
                        nok_phone_secondary if nok_phone_secondary else None,
                        nok_email if nok_email else None,
                        nok_address if nok_address else None
                    ))

                connection.commit()
                flash("Next of Kin contact details saved successfully.", "success")
                
    except Exception as e:
        flash(f"Database Error updating Emergency Contact: {str(e)}", "danger")

    return redirect(url_for("students_blueprint.manage_students"))

# ---------------------------------------------------------
# Delete Student Account
# ---------------------------------------------------------

@blueprint.route('/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    try:
        with get_db_connection() as connection:
            with connection.cursor() as cursor:
                # Optional: Delete NOK info first if foreign key constraint lacks CASCADE deletion rules
                cursor.execute("DELETE FROM student_next_of_kin WHERE student_id=%s", (student_id,))
                
                # Delete main user account row
                cursor.execute("DELETE FROM users_students WHERE id=%s", (student_id,))
                
                connection.commit()
                flash("Student account and linked emergency contact data removed.", "success")
    except Exception:
        flash("Cannot delete student. They may have linked academic, fee collection, or system tracking logs.", "danger")

    return redirect(url_for("students_blueprint.manage_students"))

# ---------------------------------------------------------
# Generic Routing
# ---------------------------------------------------------

@blueprint.route('/<template>')
def route_template(template):
    try:
        if not template.endswith('.html'):
            template += '.html'
        segment = get_segment(request)
        return render_template(f"students/{template}", segment=segment)
    except TemplateNotFound:
        return render_template('home/page-404.html'), 404
    except Exception:
        return render_template('home/page-500.html'), 500