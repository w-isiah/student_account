# -*- encoding: utf-8 -*-
import pytz
from datetime import datetime
from flask import render_template, request, redirect, url_for, flash
from jinja2 import TemplateNotFound

from apps import get_db_connection
from apps.programmes import blueprint

def get_segment(request):
    try:
        segment = request.path.split('/')[-1]
        return segment if segment != '' else 'manage_programmes'
    except:
        return None

@blueprint.route('/manage_programmes')
def manage_programmes():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    try:
        cursor.execute('SELECT COUNT(id) as total_programmes, SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_count FROM programmes')
        stats = cursor.fetchone()
        cursor.execute('SELECT * FROM programmes ORDER BY name ASC')
        programmes = cursor.fetchall()
        return render_template('programmes/list.html', stats=stats, programmes=programmes, segment='manage_programmes')
    except Exception as e:
        flash(f"Error: {str(e)}", "danger")
        return redirect(url_for('home_blueprint.index'))
    finally:
        cursor.close()
        connection.close()

@blueprint.route('/add_programme', methods=['POST'])
def add_programme():
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    if not name:
        flash("Programme Name is mandatory.", "warning")
        return redirect(url_for('programmes_blueprint.manage_programmes'))
    
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('INSERT INTO programmes (name, description, is_active) VALUES (%s, %s, 1)', (name, description))
        connection.commit()
        flash(f"Added: {name}.", "success")
    except Exception as e:
        connection.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        connection.close()
    return redirect(url_for('programmes_blueprint.manage_programmes'))

@blueprint.route('/edit_programme/<int:programme_id>', methods=['POST'])
def edit_programme(programme_id):
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    is_active = 1 if request.form.get('is_active') in ['True', '1', 'on'] else 0
    
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('UPDATE programmes SET name = %s, description = %s, is_active = %s WHERE id = %s', (name, description, is_active, programme_id))
        connection.commit()
        flash("Updated successfully.", "success")
    except Exception as e:
        connection.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        connection.close()
    return redirect(url_for('programmes_blueprint.manage_programmes'))

@blueprint.route('/delete_programme/<int:programme_id>', methods=['POST'])
def delete_programme(programme_id):
    connection = get_db_connection()
    try:
        cursor = connection.cursor()
        cursor.execute('DELETE FROM programmes WHERE id = %s', (programme_id,))
        connection.commit()
        flash("Record removed.", "success")
    except Exception as e:
        connection.rollback()
        flash(f"Error: {str(e)}", "danger")
    finally:
        connection.close()
    return redirect(url_for('programmes_blueprint.manage_programmes'))
# --- Generic Routing ---

@blueprint.route('/<template>')
def route_template(template):
    try:
        if not template.endswith('.html'):
            template += '.html'

        segment = get_segment(request)
        return render_template(f"programmes/{template}", segment=segment)

    except TemplateNotFound:
        return render_template('home/page-404.html'), 404
    except Exception:
        return render_template('home/page-500.html'), 500