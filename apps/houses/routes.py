import pytz
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from jinja2 import TemplateNotFound

from apps import get_db_connection
from apps.houses import blueprint # Ensure this matches your blueprint registration

# --- Helpers ---

def get_kampala_time():
    """Returns current time in Africa/Kampala."""
    return datetime.now(pytz.timezone("Africa/Kampala"))

def get_segment(request):
    """Extracts the current page name from the request path."""
    try:
        segment = request.path.split('/')[-1]
        return segment if segment != '' else 'manage_houses'
    except:
        return None

# --- Routes ---

@blueprint.route('/manage_houses')
def manage_houses():
    """Displays school houses and stats."""
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # 1. Aggregate House Metrics
        cursor.execute('''
            SELECT 
                COUNT(id) as total_houses,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_count
            FROM house
        ''')
        stats = cursor.fetchone()

        # 2. Fetch House List
        cursor.execute('SELECT * FROM house ORDER BY created_at DESC')
        houses = cursor.fetchall()

        return render_template(
            'houses/list.html',
            stats=stats, 
            houses=houses,
            segment='manage_houses'
        )
        
    except Exception as e:
        flash(f"Error loading House data: {str(e)}", "danger")
        return redirect(url_for('home_blueprint.index'))
    finally:
        cursor.close()
        connection.close()


@blueprint.route('/add_house', methods=['POST'])
def add_house():
    """Registers a new School House."""
    
    house_name   = request.form.get('house_name', '').strip()
    house_master = request.form.get('house_master', '').strip()
    motto        = request.form.get('motto', '').strip()

    if not house_name:
        flash("House Name is mandatory.", "warning")
        return redirect(url_for('houses_blueprint.manage_houses'))

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('''
            INSERT INTO house (house_name, house_master, motto, is_active)
            VALUES (%s, %s, %s, 1)
        ''', (house_name, house_master, motto))
        
        connection.commit()
        flash(f"Successfully registered {house_name} House.", "success")
        
    except Exception as e:
        connection.rollback()
        flash(f"Database Error: {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for('houses_blueprint.manage_houses'))


@blueprint.route('/edit_house/<int:house_id>', methods=['POST'])
def edit_house(house_id):
    """Updates an existing House record."""
    
    house_name   = request.form.get('house_name', '').strip()
    house_master = request.form.get('house_master', '').strip()
    motto        = request.form.get('motto', '').strip()
    is_active    = 1 if request.form.get('is_active') in ['True', '1', 'on'] else 0

    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('''
            UPDATE house 
            SET house_name = %s, 
                house_master = %s, 
                motto = %s, 
                is_active = %s
            WHERE id = %s
        ''', (house_name, house_master, motto, is_active, house_id))
        
        connection.commit()
        flash(f"House '{house_name}' updated successfully.", "success")
            
    except Exception as e:
        connection.rollback()
        flash(f"Error: Could not update house details. {str(e)}", "danger")
    finally:
        connection.close()

    return redirect(url_for('houses_blueprint.manage_houses'))


@blueprint.route('/delete_house/<int:house_id>', methods=['POST'])
def delete_house(house_id):
    """Removes a house record."""
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('DELETE FROM house WHERE id = %s', (house_id,))
        connection.commit()
        
        if cursor.rowcount > 0:
            flash("House record removed.", "success")
        else:
            flash("House record not found.", "warning")
            
    except Exception as e:
        connection.rollback()
        flash(f"Error: Cannot delete this house. It may be linked to students.", "danger")
    finally:
        connection.close()

    return redirect(url_for('houses_blueprint.manage_houses'))

# --- Generic Routing ---

@blueprint.route('/<template>')
def route_template(template):
    try:
        if not template.endswith('.html'):
            template += '.html'

        segment = get_segment(request)
        return render_template(f"houses/{template}", segment=segment)

    except TemplateNotFound:
        return render_template('home/page-404.html'), 404
    except Exception:
        return render_template('home/page-500.html'), 500