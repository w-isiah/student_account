import pytz
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from jinja2 import TemplateNotFound

from apps import get_db_connection
from apps.academic_years import blueprint # Ensure this matches your blueprint name

# --- Helpers ---

def get_kampala_time():
    """Returns current time in Africa/Kampala."""
    return datetime.now(pytz.timezone("Africa/Kampala"))

def get_segment(request):
    """Extracts the current page name from the request path."""
    try:
        segment = request.path.split('/')[-1]
        return segment if segment != '' else 'manage_academic_years'
    except:
        return None

# --- Routes ---

@blueprint.route('/manage_academic_years')
def manage_academic_years():
    """Displays all academic years and basic statistics."""
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # 1. Aggregate Stats
        cursor.execute('''
            SELECT 
                COUNT(id) as total_years,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_count
            FROM academic_year
        ''')
        stats = cursor.fetchone()

        # 2. Fetch all years
        cursor.execute('SELECT * FROM academic_year ORDER BY start_date DESC')
        academic_years = cursor.fetchall()

        return render_template(
            'academic_years/list.html',
            stats=stats, 
            academic_years=academic_years,
            segment='manage_academic_years'
        )
        
    except Exception as e:
        flash(f"Error loading Academic Years: {str(e)}", "danger")
        return redirect(url_for('home_blueprint.index'))
    finally:
        cursor.close()
        connection.close()


@blueprint.route('/add_academic_year', methods=['POST'])
def add_academic_year():
    """Registers a new academic year."""
    
    name       = request.form.get('name', '').strip()
    start_date = request.form.get('start_date')
    end_date   = request.form.get('end_date')

    if not all([name, start_date, end_date]):
        flash("Missing required fields: Name, Start Date, and End Date are mandatory.", "warning")
        return redirect(url_for('academic_years_blueprint.manage_academic_years'))

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('''
            INSERT INTO academic_year (name, start_date, end_date, is_active)
            VALUES (%s, %s, %s, 1)
        ''', (name, start_date, end_date))
        
        connection.commit()
        flash(f"Successfully registered academic year: {name}.", "success")
        
    except Exception as e:
        connection.rollback()
        flash(f"Database Error: {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for('academic_years_blueprint.manage_academic_years'))


@blueprint.route('/edit_academic_year/<int:year_id>', methods=['POST'])
def edit_academic_year(year_id):
    """Updates an existing academic year."""
    
    name       = request.form.get('name', '').strip()
    start_date = request.form.get('start_date')
    end_date   = request.form.get('end_date')
    is_active  = 1 if request.form.get('is_active') in ['True', '1', 'on'] else 0

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('''
            UPDATE academic_year 
            SET name = %s, 
                start_date = %s, 
                end_date = %s, 
                is_active = %s
            WHERE id = %s
        ''', (name, start_date, end_date, is_active, year_id))
        
        connection.commit()
        flash(f"Academic Year '{name}' updated successfully.", "success")
            
    except Exception as e:
        connection.rollback()
        flash(f"Error: Could not update record. {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for('academic_years_blueprint.manage_academic_years'))


@blueprint.route('/delete_academic_year/<int:year_id>', methods=['POST'])
def delete_academic_year(year_id):
    """Removes an academic year record."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('DELETE FROM academic_year WHERE id = %s', (year_id,))
        connection.commit()
        
        if cursor.rowcount > 0:
            flash("Academic Year removed from the system.", "success")
        else:
            flash("Record not found.", "warning")
            
    except Exception as e:
        connection.rollback()
        flash(f"Error: Cannot delete this year (it may be linked to students or classes).", "danger")
    finally:
        connection.close()

    return redirect(url_for('academic_years_blueprint.manage_academic_years'))

# --- Generic Routing ---

@blueprint.route('/<template>')
def route_template(template):
    try:
        if not template.endswith('.html'):
            template += '.html'

        segment = get_segment(request)
        return render_template(f"academic_years/{template}", segment=segment)

    except TemplateNotFound:
        return render_template('home/page-404.html'), 404
    except Exception:
        return render_template('home/page-500.html'), 500