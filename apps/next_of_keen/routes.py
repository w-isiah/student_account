import pytz
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from jinja2 import TemplateNotFound
from werkzeug.security import generate_password_hash

from apps import get_db_connection
# Updated to bind directly to your new Blueprint declaration instance
from apps.next_of_keen import blueprint 

# --- Helpers ---

def get_kampala_time():
    return datetime.now(pytz.timezone("Africa/Kampala"))

def get_segment(request):
    try:
        segment = request.path.split('/')[-1]
        return segment if segment != '' else 'manage_students'
    except:
        return None

# ---------------------------------------------------------
# Manage Student Accounts & Next of Kin
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

                # 2. STUDENT LIST (Enhanced with LEFT JOIN for Next of Kin data)
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
    
    # Validation
    if not reg_no or not first_name or not last_name:
        flash("Registration Number and Names are required.", "warning")
        return redirect(url_for("next_of_keen_blueprint.manage_students"))

    # Security: Registration Number is the Password
    hashed_password = generate_password_hash(reg_no)

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            INSERT INTO users_students
            (reg_no, first_name, last_name, other_name, email, password, gender, account_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'Active')
        """, (
            reg_no, first_name, last_name, other_name, 
            email, hashed_password, gender
        ))
        connection.commit()
        flash(f"Account for {reg_no} created. Password is set to Registration Number.", "success")
    except Exception as e:
        connection.rollback()
        flash(f"Database Error: {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for("next_of_keen_blueprint.manage_students"))

# ---------------------------------------------------------
# Edit Student Account
# ---------------------------------------------------------

@blueprint.route('/edit_student/<int:student_id>', methods=['POST'])
def edit_student(student_id):
    first_name = request.form.get("first_name")
    last_name = request.form.get("last_name")
    other_name = request.form.get("other_name")
    email = request.form.get("email")
    gender = request.form.get("gender")
    status = request.form.get("account_status")

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("""
            UPDATE users_students
            SET first_name=%s, last_name=%s, other_name=%s, 
                email=%s, gender=%s, account_status=%s
            WHERE id=%s
        """, (first_name, last_name, other_name, email, gender, status, student_id))

        connection.commit()
        flash(f"Student record updated successfully.", "success")
    except Exception as e:
        connection.rollback()
        flash(f"Error updating record: {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for("next_of_keen_blueprint.manage_students"))

# ---------------------------------------------------------
# Update Next of Kin
# ---------------------------------------------------------

@blueprint.route('/update_nok/<int:student_id>', methods=['POST'])
def update_nok(student_id):
    nok_name = request.form.get("nok_name", "").strip()
    nok_relationship = request.form.get("nok_relationship")
    nok_phone = request.form.get("nok_phone", "").strip()
    nok_phone_secondary = request.form.get("nok_phone_secondary", "").strip() or None
    nok_email = request.form.get("nok_email", "").strip() or None
    nok_address = request.form.get("nok_address", "").strip() or None

    if not nok_name or not nok_phone or not nok_relationship:
        flash("Guardian name, relationship, and primary phone number are mandatory.", "warning")
        return redirect(url_for("next_of_keen_blueprint.manage_students"))

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        
        # Check if an emergency contact entry already exists for this student
        cursor.execute("SELECT id FROM student_next_of_kin WHERE student_id = %s", (student_id,))
        existing_record = cursor.fetchone()

        if existing_record:
            # Update operational contact details
            cursor.execute("""
                UPDATE student_next_of_kin 
                SET full_name=%s, relationship=%s, phone_primary=%s, 
                    phone_secondary=%s, email=%s, residential_address=%s
                WHERE student_id=%s
            """, (nok_name, nok_relationship, nok_phone, nok_phone_secondary, nok_email, nok_address, student_id))
        else:
            # Create a brand new record if this is the first entry
            cursor.execute("""
                INSERT INTO student_next_of_kin 
                (student_id, full_name, relationship, phone_primary, phone_secondary, email, residential_address)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (student_id, nok_name, nok_relationship, nok_phone, nok_phone_secondary, nok_email, nok_address))

        connection.commit()
        flash("Next of Kin details saved successfully.", "success")
    except Exception as e:
        connection.rollback()
        flash(f"Error processing guardian contact: {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for("next_of_keen_blueprint.manage_students"))

# ---------------------------------------------------------
# Delete Student Account
# ---------------------------------------------------------

@blueprint.route('/delete_student/<int:student_id>', methods=['POST'])
def delete_student(student_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM users_students WHERE id=%s", (student_id,))
        connection.commit()
        flash("Student account has been permanently removed.", "success")
    except Exception as e:
        connection.rollback()
        flash("Cannot delete student. They may have linked academic records.", "danger")
    finally:
        connection.close()

    return redirect(url_for("next_of_keen_blueprint.manage_students"))

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